#!/usr/bin/env python3

from __init__ import __version__, LASTMODIFIED_UNIXTIME, LASTMODIFIED, LASTMODIFIED_DATE
from util import read_lines,  datetime_representation, already_passed, run_at, create_choice_html

import asyncio, asyncpg

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.middleware import Middleware
from starlette.templating import Jinja2Templates
from starlette.responses import  JSONResponse
from sse_starlette.sse import EventSourceResponse

from os import walk
from os import getenv
from babel import Locale
from asgi_babel import BabelMiddleware, gettext, current_locale, select_locale_by_request

import json, re, regex, logging

from dotenv import load_dotenv
import random as pseudo_random
from random import SystemRandom
from pgpdump import AsciiData

from datetime import datetime
from collections import namedtuple

from dateutil.parser import isoparse, parse

from poll import poll_choices
from exceptions import VoteRejectException

pseudo_id = namedtuple('pseudo_id', ['pseudonym', 'code', 'cryptonym'])

def get_pseudonym_list(voter_count, word_list, cryptonyms = False, salt_amount = None):
    """
    Creating the randomized list of pseudonyms was initially the main feature of initial Uduloor version of pseudonymous voting routine, but it is currently provided here mostly for convenience. Ideally you should select the pseudonyms far away from the bulletin board service in order to avoid any manipulation of the votes. Selection process currently enables of selection pseudonyms and "salting" them with a number to create memorizable pairs of text and numbers following the pattern of `user123`. In a real polling situation you should provide pseudonyms separately and then maybe include hashed list of pseudonyms as gatekeeper for vote collecting mechanism to make sure it is not manipulated by spamming or similar.
    """
    random_words = random.sample(word_list, voter_count)
    
    if cryptonyms:
        if salt_amount is None:
            salt_amount = 3

        random_keys = [random.choice(range(10**salt_amount//10, 10**salt_amount)) for x in range(voter_count)]
    
    pseudo_list = []
    
    for i in range(voter_count):
        
        if not cryptonyms:
            pseudo_list.append(pseudo_id(random_words[i], None, None))
        else:
            pseudo_list.append(pseudo_id(random_words[i], str(random_keys[i]), random_words[i] + str(random_keys[i])))
        
    return pseudo_list
        
def announce_event(name, timestamp, channel, started = None):
    """
    Announce events to every client in channel.
    """
    if channel in app.sessions:
        res = {"state": name}
        res["data"] = {}
        res["data"]["timestamp"] = datetime_representation(timestamp)
        res["data"]["token"] = channel
        if started is not None:
            res["data"]["started"] = datetime_representation(started)
        count = 0
        for q in app.sessions[channel]['queue']:
            q.put_nowait(res)
            count += 1

def announce_event_individual(name, timestamp, cq, channel, started = None):
    """
    Announce already past events for individual clients mostly on connecting the feed.
    """
    res = {"state": name}
    res["data"] = {}
    res["data"]["timestamp"] = datetime_representation(timestamp)
    res["data"]["token"] = channel
    if started is not None:
        res["data"]["started"] = datetime_representation(started)
    cq.put_nowait(res)

async def event_publisher(req, channel, cq, start, end, encrypt_ballots = False):
    """
    Each client will have this publisher loop that will self destruct on disconnect. If no clients are listening to specifig election feed any more, the channel itself will be removed.
    """
    client_host = req.client.host
    if client_host is None:
        client_host = "/.../"
    try:
        while True:
            data_dict = await cq.get()
            
            if "record" in data_dict:
                data_dict['data'] = data_dict.pop('record')

            yield json.dumps(data_dict)
    except asyncio.CancelledError as e:
        logging.info(f"Client disconnected, releasing {client_host} from {channel}.")
        
    app.sessions[channel]['queue'].remove(cq)
    del cq
    if len(app.sessions[channel]['queue']) == 0:
        if 'announce-end' in app.sessions[channel]:
            app.sessions[channel]['announce-end'].cancel()
            del app.sessions[channel]['announce-end']
        if 'announce-start' in app.sessions[channel]:
            app.sessions[channel]['announce-start'].cancel()
            del app.sessions[channel]['announce-start']
        del app.sessions[channel]
        logging.info(f"Channel {channel} empty and deleted, {len(app.sessions)} channels with {sum(len(app.sessions[c]['queue']) for c in app.sessions)} queues remain.")

def database_listener(*args):
    """
    Listens to database notifications and relays the messages to designated channels. There is one notfication service for all the votings in order to keep the connection pool minimal.
    """
    connection, pid, channel, payload = args
    data = json.loads(payload)
    
    if data['type'] == "vote_added":
        channel = data['token']
        if channel in app.sessions:
            res = {"state": "incoming-vote"}
            res["record"] = data["record"]
            res["token"] = channel
            count = 0
            for q in app.sessions[channel]['queue']:
                q.put_nowait(res)
                count += 1
            
    elif data['type'] == "bulletin_created":
        data = data['record']
        
        token, finish, limit_multi, limit_unlisted, limit_invalid = data['token'], isoparse(data['finish']), data['limit_multi'], data['limit_unlisted'], data['limit_invalid']
        
async def subscribe_bulletin(req):
    """
    Bulletin board of incoming votes is an essential feature and is displayed based on `token` of a poll. The feed is provided as EventSource feed in JSON and includes also poll status announcements.
    """
    token = req.path_params['token']
    
    async with app.state.pool.acquire() as con:
        res = await con.fetchrow("SELECT token, start, finish, encrypt_ballots FROM pseudo.bulletin WHERE id = pseudo.get_bulletin_id($1)", token)

    channel, start, end, encrypt_ballots = res['token'], res['start'], res['finish'], res['encrypt_ballots']

    cq = asyncio.Queue()

    if channel not in app.sessions or len(app.sessions[channel]['queue']) == 0:
        app.sessions[channel] = {}
        app.sessions[channel]['queue'] = [cq]
    else:
        app.sessions[channel]['queue'].append(cq)

    # kui tuleb uus kuulaja ja kanalil on juba teavitus soolas, siis saab ta selle sealt
    # juba möödunud tähtaja teavituse saab uutele kohe edastada
    # tuleb lisada kanalile ootele ainult tulevad teavitused pärast kontrolli, kas pole juba soolas
    if already_passed(end):
        announce_event_individual("end", end, cq, channel)
    elif already_passed(start):
        announce_event_individual("wait-end", end, cq, channel, start)
        if 'announce-end' not in app.sessions[channel]:
            app.sessions[channel]['announce-end'] = run_at(end, announce_event, "end", end, channel)
    elif not already_passed(start):
        announce_event_individual("wait-start", start, cq, channel)
        if 'announce-start' not in app.sessions[channel]:
            app.sessions[channel]['announce-start'] = run_at(start, announce_event, "wait-end", end, channel, start)
        if 'announce-end' not in app.sessions[channel]: # pole vaja kontrollida tegelt
            app.sessions[channel]['announce-end'] = run_at(end, announce_event, "end", end, channel)

    return EventSourceResponse(event_publisher(req, channel, cq, start, end, encrypt_ballots = encrypt_ballots))
    
def convert_to_token(name):
    """
    Convert name to a slug. Maybe should replace with dedicated slugify module.
    """
    name = re.sub('-+', '-', re.sub("\W", "", name.strip().lower().replace(" ","_").replace("-","_")).replace("_", "-"))
    return name

async def get_bulletin_token(req):
    """
    Coordinate available tokens with database.
    """
    body = dict(await req.form())
    res = []
    tokenized_name = convert_to_token(body["name"])
    async with app.state.pool.acquire() as con:
        token = await con.fetchval("SELECT pseudo.generate_uid(12, 'pseudo.bulletin'::regclass)")
        name = await con.fetchval("SELECT pseudo.suggest_name($1, 3, 'pseudo.bulletin'::regclass)", tokenized_name)
    return JSONResponse(dict({"token": token, "name": name}))

async def get_bulletin_name(req):
    """
    Coordinate available names with database.
    """
    body = dict(await req.form())
    res = []
    tokenized_name = convert_to_token(body["name"])
    async with app.state.pool.acquire() as con:
        res = await con.fetchval("SELECT pseudo.suggest_name($1, 3, 'pseudo.bulletin'::regclass)", tokenized_name)
    return JSONResponse(dict({"name": res}))

def data_state(label, data = {}, token = "?"):
    """
    Content of data events about voting progress generated by the main voting process routine.
    """
    return json.dumps(dict(state=label, data=data))

async def provide_voterlist_to_bulletin(pseudo_list, voterhash_type = None, bulletin_id = None):
    """
    Create voterlist for the poll in case `provide_voterlist` has been selected among bulletin board restrictions. Otherwise the voterlist will be not recorded to database, except for emergency fallback (see `encrypt_voterlist_under_embargo`).
    """
    if voterhash_type is None or len(pseudo_list) == 0:
        return
        
    async with app.state.pool.acquire() as con:
           
        if voterhash_type not in ['pseudonym', 'cryptonym']:
            if pseudo_list[0].cryptonym:
                hash_field = 'cryptonym'
            else:
                hash_field = 'pseudonym'
        
        if voterhash_type in ['pseudonym', 'cryptonym']:
            insert_plaintext = await con.prepare("INSERT INTO pseudo.voterlist (pseudonym, code, cryptonym, bulletin_id) VALUES ($1, $2, $3, $4)")
            for p in pseudo_list:
                await insert_plaintext.fetch(p.pseudonym, p.code, p.cryptonym, bulletin_id)
        else:
            insert_hash = await con.prepare(f"INSERT INTO pseudo.voterlist (hash, bulletin_id) SELECT encode(pseudo.digest($1, '{voterhash_type}'), 'hex'), $2")
            for p in pseudo_list:
                await insert_hash.fetch(getattr(p, hash_field), bulletin_id)

    return
    
async def encrypt_voterlist_under_embargo(pseudo_list, use_public_key, pubkey_id, bulletin_id):
    """
    Encrypting voterlist is done on demand with pgp public key provided at `use_public_key` in bulletin board access restrictions dialog or as a fallback when system breaks down for some reason. If voterlist is encrypted as a fallback, public key of the bulletin board system itself is used in order to decrypt the voterlist with private key after embargo, that is, when the election is over. Currently encrypting the voterlist is not proper feature of exemplified voting scheme, because encryping voterlist makes sense if it is done as completely separate process and maybe providing voterlist to bulletin board in already encrypted form (hashed pseydonyms and/or full encrypted voterlist to be decrypted after election is over).
    """
    if len(pseudo_list) == 0:
        return None
    
    pgp_id = None
    
    async with app.state.pool.acquire() as con:
        
        if pseudo_list[0].cryptonym:
            full_voterlist = "\n".join(list(x.cryptonym for x in pseudo_list))
        else:
            full_voterlist = "\n".join(list(x.pseudonym for x in pseudo_list))

        pgp_id = await con.fetchval("UPDATE pseudo.bulletin SET full_voterlist = pseudo.armor(pseudo.pgp_pub_encrypt($1, pseudo.dearmor($2))), pubkey_id = $3 WHERE id = $4 RETURNING pubkey_id", full_voterlist, use_public_key, pubkey_id, bulletin_id)
    
    return pgp_id

async def get_ballot_count(bulletin_id):
    """
    Report ballot count for a poll/election.
    """
    ballot_count = None
    async with app.state.pool.acquire() as con:

        ballot_count = await con.fetchval("SELECT COUNT(id) FROM pseudo.vote WHERE bulletin_id = $1", bulletin_id)
    
    return ballot_count

async def collector(req):
    """
    Vote collector is a web page where votes are collected for single pre-defined election. Election status and bulletin board of incoming votes are displayed in real time to provide voter transparancy and hands on understanding of the process. Vote collector is the most critical part of the system from viewpoint of technical universality and has to be usable without Javascript or any other fancy web technology. Currently vote collector is somewhat tested against HTTPS capable versions of Lynx, Netscape Navigator and different versions Android/iPhone.
    """
    token = req.path_params['token']

    async with app.state.pool.acquire() as con:
        bulletin_id = await con.fetchval("SELECT pseudo.get_bulletin_id($1)", token)

    if bulletin_id is None:
        return templates.TemplateResponse('empty.html', {'request': req, 'locale': current_locale.get().language})

    pseudonym = ""    
    if 'pseudonym' in req.path_params:
        pseudonym = req.path_params['pseudonym']
    """elif len(req.query_params.keys()) > 0:
        p = next(iter(req.query_params.keys()))
        if len(p) > 0:
            pseudonym = p
    """
    
    choices, title, start, end, created, in_voterlist, block_unlisted, encrypt_ballots, mute_unlisted, limit_choices = await data_for_bulletin(token, bulletin_id, pseudonym)
       
    if block_unlisted and not in_voterlist:
        return templates.TemplateResponse('restrict.html', {'request': req, 'locale': current_locale.get().language})
    
    if title is None:
        title = ""
        
    transaction_timestamp, latest_timestamp, started, ended, votes = await votes_until_now(token, bulletin_id, start, end, encrypt_ballots)
    
    timing = {'transaction': datetime_representation(transaction_timestamp), 'latest': datetime_representation(latest_timestamp) if latest_timestamp is not None else None, 'started': started, 'ended': ended}
    
    return templates.TemplateResponse('collect.html', {'request': req, 'pseudonym': pseudonym, 'votes': votes, 'timing': timing, 'choices': choices, 'bulletin_title': title, 'mute_unlisted': mute_unlisted, 'limit_choices': limit_choices, 'metadata_params': {'created': created, "start": start, "end": end, "choices": choices, "token": token}, "token": token, 'locale': current_locale.get().language})

async def votes_until_now(token = None, bulletin_id = None, start = None, end = None, encrypt_ballots = None):
    """
    Displays bulletin board in TEXT format up to current moment. This is used as an easy way to display bulletin board history in vote collector TEXTAREA without extra work on client side, maybe ideally should be replaced with feeding the events from certain point in history and ensuring there are no gaps in bulletin board.
    """
    now = datetime.now().astimezone()
        
    if encrypt_ballots and (end is None or end is not None and now < end):
        ballot_field = "content_hash"
    else:
        ballot_field = "content"

    async with app.state.pool.acquire() as con:
        if bulletin_id is not None:
            async with con.transaction():
                res = await con.fetch(f"SELECT pseudonym, {ballot_field} as content, added, number FROM pseudo.vote WHERE bulletin_id = $1 ORDER BY added ASC", bulletin_id)
                transaction_timestamp = await con.fetchval("SELECT TRANSACTION_TIMESTAMP()")
        else:
            async with con.transaction():
                res = await con.fetch(f"SELECT pseudonym, {ballot_field} as content, added, number FROM pseudo.vote WHERE bulletin_id = pseudo.get_bulletin_id($1) ORDER BY added ASC", token)
                transaction_timestamp = await con.fetchval("SELECT TRANSACTION_TIMESTAMP()")
    
    votes = ""
    
    latest_timestamp = None
    server_now = transaction_timestamp
    started = ended = False
    start_str = "=== STARTED: " + datetime_representation(start) + " ===" + '\n'
    end_str = "=== FINISHED: " + datetime_representation(end) + " ===" + '\n'
    
    for v in res:
        if start is not None and not started:
            if v['added'] > start:
                votes += start_str
                started = True
        if end is not None and not ended:
            if v['added'] > end:
                votes += end_str
                ended = True
        votes += f"{v['number']}) {v['pseudonym']}: {v['content'] if v['content'] is not None else ''}\n"
        latest_timestamp = v['added']
    
    if not started and server_now >= start:
        votes += start_str
        started = True

    if started and not ended and server_now >= end:
        votes += end_str
        ended = True
        
    votes += "=== BULLETIN: " + datetime_representation(transaction_timestamp) + " ===" + '\n'
        
    return transaction_timestamp, latest_timestamp, started, ended, votes

async def data_for_bulletin_feed(token):
    """
    If conduct election process is disturbed, this enables to condtinue observers still getting the needed audit information in the end of election. This is mostly for convenience and fallback to ensure continuity of the process in case of milder technical disturbances.
    """
    decrypted_voterlist = None
               
    async with app.state.pool.acquire() as con:
        bulletin_token, bulletin_id, start, finish, created, choices, name, title, voterhash_type, ballot_type, voter_count, full_voterlist, pub_key_id = await con.fetchrow("SELECT token, id, start, finish, created, choices, name, title, voterhash_type, ballot_type, voter_count, full_voterlist, pubkey_id FROM pseudo.bulletin WHERE id = pseudo.get_bulletin_id($1)", token)
        
        if pub_key_id == public_key_id:
            logging.info(f"Fetching and decrypting embargoed voterlist crypted with {pub_key_id}.")
            decrypted_voterlist = await con.fetchval("SELECT pseudo.pgp_pub_decrypt(pseudo.dearmor(full_voterlist), pseudo.dearmor($1)) FROM pseudo.bulletin WHERE id = $2", "\n".join(private_key), bulletin_id)
            
    return bulletin_token, bulletin_id, start, finish, created, choices, name, title, voterhash_type, ballot_type, voter_count, full_voterlist, pub_key_id, decrypted_voterlist
        
async def data_for_bulletin(token = None, bulletin_id = None, pseudonym = ""):
    """
    Returns basic data for displaying election for a voter or an auditor in vote collector web page or audit web page. Also formats the choices in HTML if they are predefined.
    """
    in_voterlist = True
    choices, title, start, end, created = None, "", None, None, None
    
    async with app.state.pool.acquire() as con:
        if bulletin_id is not None:
            res = await con.fetchrow("SELECT id, choices, title, start, finish, created, voterhash_type, block_unlisted, encrypt_ballots, mute_unlisted, limit_choices FROM pseudo.bulletin WHERE id = $1", bulletin_id)
        else:
            res = await con.fetchrow("SELECT id, choices, title, start, finish, created, voterhash_type, block_unlisted, encrypt_ballots, mute_unlisted, limit_choices FROM pseudo.bulletin WHERE id = pseudo.get_bulletin_id($1)", token)

        if res is None:
            return None, title, start, end, created, None, None, None, None, None
    
        bulletin_id, choices, title, start, end, created, voterhash_type, block_unlisted, encrypt_ballots, mute_unlisted, limit_choices = res['id'], res['choices'], res['title'], res['start'], res['finish'], res['created'], res['voterhash_type'], res['block_unlisted'], res['encrypt_ballots'], res['mute_unlisted'], res['limit_choices']
        
        if block_unlisted:
            if len(pseudonym) == 0:
                in_voterlist = False
            else:
                in_voterlist = await is_in_voterlist(con, voterhash_type, bulletin_id, pseudonym)
            
            if not in_voterlist:
                return "", title, start, end, created, in_voterlist, block_unlisted, encrypt_ballots, mute_unlisted, limit_choices
                
    c = []
    
    if choices is not None and len(choices) > 0:
        c = json.loads(choices)
    
    html = ""
    index = 1
    
    for r in c: 
        if 'ordered' not in r:
            r['ordered'] = False

        if r['max'] == 1:
            html += create_choice_html(r, index, "radio", "radio")
            index += 1

        elif r['max'] > 1 and not r['ordered']:
            html += create_choice_html(r, index, "check", "checkbox")
            index += 1
                        
        elif r['max'] > 1 and r['ordered']:
            html += create_choice_html(r, index, "multi", "checkbox", ordered=True)
            index += 1
        
    return html, title, start, end, created, in_voterlist, block_unlisted, encrypt_ballots, mute_unlisted, limit_choices

async def is_in_voterlist(con, voterhash_type, bulletin_id, pseudonym):
    """
    Convenience method to detect if pseudonym is in voterlist.
    """
    in_voterlist = False

    if voterhash_type in ["pseudonym", "cryptonym"]:
        in_voterlist = await con.fetchval(f"SELECT EXISTS (SELECT 1 FROM pseudo.voterlist WHERE bulletin_id = $1 AND {voterhash_type} = $2)", bulletin_id, pseudonym)
    else:
        in_voterlist = await con.fetchval(f"SELECT EXISTS (SELECT 1 FROM pseudo.voterlist WHERE bulletin_id = $1 AND hash = encode(pseudo.digest($2, '{voterhash_type}'), 'hex'))", bulletin_id, pseudonym)

    return in_voterlist
    
def conforms_to_choices(ballot, c):
    """
    If creator of elections has selected to refrain from storing invalid ballots, this is used to fuzzy match them to provided voter choices.
    """
    r = r"^"
    for x in c:
        r += x.fuzzy_regex()
    
    try:
        # fuzzier is more interesting,
        # but current implementation tends to fail, so we need this timeout
        res = regex.fullmatch(r, ballot, flags = regex.IGNORECASE, timeout = 0.1)
        
    except TimeoutError as e:
        logging.info(f"Fuzzy matching failed {e}")
        r = r"^"
        for x in c:
            r += x.regex()
        
        res = re.fullmatch(r, ballot, regex.IGNORECASE)
    
    return res is not None    
    
def normalize_ballot_text(text):
    """
    Currently only replaces newlines with backslashes for better readability in the context of plain text bulletin board.
    """
    return text.replace('\r\n', '\\').replace('\n', '\\').replace('\r', '\\')

async def process_vote(req):
    """
    Makes sense of the submitted vote and returns a receipt. This is heavily based on restrictions defined by creator of the poll. Normally the receipt is returned as JSON, but for explicitly defined noscript clients HTML page with receipt is displayed instead. Most of this playing with restrictions is educational and shouldn't be used in normal small scale elections where people trust each other enough to not opt for exhaustive technical manipulations.
    """
    body = dict(await req.form())
    
    encrypt_ballots = None
    
    if "content" in body:
        content = normalize_ballot_text(body["content"].strip())
    else:
        content = None
    
    bulletin_token = None
    if 'bulletin_token' in body:
        bulletin_token = body['bulletin_token']
        
    if 'noscript_client' in body:
        noscript_client = bool(body['noscript_client'])
    else:
        noscript_client = False

    pseudonym = ""
    if "pseudonym" in body and len(body["pseudonym"].strip()) > 0:
        pseudonym = body["pseudonym"].strip()
      
    receipt = {"pseudonym": pseudonym, "ballot": content}
    
    try:
        
        if bulletin_token is None or len(bulletin_token) == 0:
            receipt["reject"] = "no_token"
            raise VoteRejectException(f"NO TOKEN")            
            
        async with app.state.pool.acquire() as con:
            
            bulletin_id, bulletin_name, voterhash_type, title, start, finish, ballot_type, choices, reject_multi, personal_ballot, limit_choices, reject_invalid, reject_unlisted, mute_unlisted, block_unlisted, limit_invalid, limit_unlisted, limit_multi, encrypt_ballots = await con.fetchrow("SELECT id, name, voterhash_type, title, start, finish, ballot_type, choices, reject_multi, personal_ballot, limit_choices, reject_invalid, reject_unlisted, mute_unlisted, block_unlisted, limit_invalid, limit_unlisted, limit_multi, encrypt_ballots FROM pseudo.bulletin WHERE id = pseudo.get_bulletin_id($1)", bulletin_token)
            
            if bulletin_name is not None and len(bulletin_name) > 0 and bulletin_token != bulletin_name:
                receipt["bulletin-name"] = bulletin_name
                
            receipt['bulletin_token'] = bulletin_token

            if pseudonym == "":
                receipt["reject"] = "no_pseudonym"
                raise VoteRejectException(f"NO PSEUDONYM")
            
            if reject_unlisted:
                
                receipt["state"] = "reject_unlisted"
                in_voterlist = await is_in_voterlist(con, voterhash_type, bulletin_id, pseudonym)
                if not in_voterlist:
                                                                
                    receipt["reject"] = receipt.pop("state")
                    raise VoteRejectException(f"NOT IN VOTERLIST: {pseudonym}")
                    
            if reject_multi or limit_multi:
                
                receipt["state"] = "detect_multi"
                already_voted = await con.fetchval(f"SELECT EXISTS (SELECT 1 FROM pseudo.vote WHERE bulletin_id = $1 AND pseudonym = $2)", bulletin_id, pseudonym)
                if already_voted:
                    
                    if reject_multi:
                        receipt["state"] = "reject_multi"
                        raise VoteRejectException(f"ALREADY VOTED: {pseudonym}")
                    
                del receipt["state"]
            
            c = []
            
            if reject_invalid:

                if len(c) == 0 and choices is not None and len(choices) > 0:                
                    for x in json.loads(choices):
                        c.append(poll_choices(**x))

                receipt["state"] = "reject_invalid"
                if not conforms_to_choices(content, c):
                                        
                    receipt["reject"] = receipt.pop("state")
                    raise VoteRejectException(f"INVALID BALLOT")
                
            # was not rejected
            receipt.pop("reject", None)
            
            receipt["state"] = "insert"
            if encrypt_ballots:
                                    
                res = await con.fetchrow(f"WITH ins (added, id, bulletin_id, content, pseudonym) AS (INSERT INTO pseudo.vote (bulletin_id, pseudonym, content, content_hash) SELECT $1, $2, $3, encode(pseudo.digest($4 || ' +' || to_char(transaction_timestamp(),'US'), '{ballot_type}'), 'hex') RETURNING added, id, bulletin_id, content, pseudonym, content_hash) SELECT ins.added, ins.id, ins.bulletin_id, ins.content, ins.pseudonym, ins.content_hash, bulletin.token, bulletin.name, bulletin.title, bulletin.start, bulletin.finish FROM ins JOIN pseudo.bulletin ON bulletin.id = ins.bulletin_id", bulletin_id, pseudonym, content, content if content is not None else "")
            
            else:
                
                res = await con.fetchrow("WITH ins (added, id, bulletin_id, content, pseudonym) AS (INSERT INTO pseudo.vote (bulletin_id, pseudonym, content) VALUES ($1, $2, $3) RETURNING added, id, bulletin_id, content, pseudonym, content_hash) SELECT ins.added, ins.id, ins.bulletin_id, ins.content, ins.pseudonym, ins.content_hash, bulletin.token, bulletin.name, bulletin.title, bulletin.start, bulletin.finish FROM ins JOIN pseudo.bulletin ON bulletin.id = ins.bulletin_id", bulletin_id, pseudonym, content)
                    
            receipt["state"] = "recorded"
            receipt["timestamp"] = str(res["added"])
                
            if encrypt_ballots:
                receipt['hash'] = res['content_hash']
            
    except VoteRejectException as e:
        
        logging.info(f"Exception adding vote @{bulletin_token}: {e}")
        
        if not "state" in receipt:
            receipt["state"] = "error"
        else:
            x = receipt["state"]
            receipt["state"] = "error"
            receipt["during"] = x
            
        if not "timestamp" in receipt:
            receipt['timestamp'] = datetime_representation()            

    if noscript_client and bulletin_token is not None:
        transaction_timestamp, latest_timestamp, started, ended, votes = await votes_until_now(bulletin_token, bulletin_id, start, finish, encrypt_ballots)
        return templates.TemplateResponse('voted.html', {'request': req, 'pseudonym': pseudonym, 'ballot': content, 'votes': votes, 'bulletin_title': title, 'receipt': receipt, 'locale': current_locale.get().language})
    
    return JSONResponse(receipt)

async def distributor_home(req):
    """
    This is a home page for bulletin board, currently enabling to create an election for educational or testing purposes, ideally should be accepting voterlist hashes or similar for elections already defined elsewhere.
    """
    return templates.TemplateResponse('index.html', {'request': req, 'locale': current_locale.get().language, 'candidates': pseudo_random.sample(candidates, pseudo_random.randrange(5,12)), 'public_key': "\r\n".join(public_key)}, headers={'Last-Modified': formatdate(LASTMODIFIED_UNIXTIME, usegmt=True)})

async def audit_bulletin(req):
    """
    Opens independent audit feed for an election where auditors will be provided data needed to audit the elections of tally the votes. The data displayed is the same displayed on the main election process dashboard except the e-mail sending process.
    """
    token = req.path_params['token']
    
    choices, title, start, end, created, in_voterlist, block_unlisted, encrypt_ballots, mute_unlisted, limit_choices = await data_for_bulletin(token)
    
    if start is None:
        return templates.TemplateResponse('empty.html', {'request': req, 'locale': current_locale.get().language})
        
    return templates.TemplateResponse('audit.html', {'request': req, 'locale': current_locale.get().language, 'token': token, 'bulletin_title': title, 'metadata_params': {'created': created, "start": start, "end": end, "token": token}})
    
async def sitemap(req):
    """
    Dynamically generated sitemap.
    """
    return templates.TemplateResponse('sitemap.xml', {'request': req}, headers={'Content-Type': 'application/xml'})
    
async def robots(req):
    """
    Dynamically generated robots.txt.
    """
    return templates.TemplateResponse('robots.txt', {'request': req}, headers={'Content-Type': 'text/plain'})

person_responsible = {
    "@type": "Person",
    "name": "Märt Põder",
    "sameAs": "https://www.wikidata.org/wiki/Q16404899"
    }

organizer = {
    "@type": "Organization",
    "name": "Infoaed OÜ",
    "url": "https://infoaed.ee/"
    }
        
def get_json_metadata(req, name, alt_name, title, description, params = {}, page = "home"):
    """
    Provide schema.org style JSON-LD metadata.
    """
    if page == "home":
        return metadata_for_home(req, name, alt_name, title, description)
    if page == "bulletin":
        return metadata_for_bulletin(req, name, alt_name, title, description, params)
    if page == "audit":
        return metadata_for_audit(req, name, alt_name, title, description, params)
        
    return None

def metadata_for_home(req, name, alt_name, title, description):
    """
    JSON-LD metadata for home.
    """
    metadata = {
        "@context" : "http://schema.org",
        "@type" : "WebPage",
        "mainEntityOfPage": {
            "@type": "WebSite",
            "isAccessibleForFree": True,
            "name": name,
            "alternateName": alt_name,
            "description": description,
            "url": str(req.url_for("root")),
            "author" : person_responsible,
            "datePublished": parse("Wed, 5 Jan 2022 16:27:35 +0200").isoformat()
        },
        "name": name + ": " + title,
        "description": description,
        "inLanguage" : {"@type" : "Language", 'name': locale_names[current_locale.get().language]["en"],
            "alternateName": current_locale.get().language},
        "dateModified" : LASTMODIFIED.isoformat(),
        "url" : str(req.url)
    }

    return metadata

def metadata_for_bulletin(req, name, alt_name, title, description, params):
    """
    JSON-LD metadata for bulletin boards.
    """
    now = datetime.now().astimezone()

    metadata = {
        "@context" : "http://schema.org",
        "@type" : "Event",
        "@id": str(req.url),
        "name": title,
        "alternateName": alt_name,
        "description": description,
        "mainEntityOfPage": {
            "@type": "WebPage",
            "name": name + ": " + title,
            "alternateName": alt_name,
            "description": description,
            "url": str(req.url),
            "datePublished": params['created'].isoformat()
        },
        "inLanguage" : {"@type" : "Language",
            'name': locale_names[current_locale.get().language]["en"],
            "alternateName": current_locale.get().language},
        "doorTime": params['created'].isoformat(),
        "startDate": params['start'].isoformat(),
        "endDate": params['end'].isoformat(),
        "eventStatus": "EventScheduled",
        "eventAttendanceMode": "OnlineEventAttendanceMode",
        "isAccessibleForFree": True,
        "location": {
            "@type": "VirtualLocation",
            "name": name,
            "url": str(req.url)
        },
        "image": str(req.url_for("root")) + "static/logo.jpg",
        "url" : str(req.url),
        "organizer": organizer
    }
    
    return metadata

def metadata_for_audit(req, name, alt_name, title, description, params):
    """
    JSON-LD metadata for audit dashboards.
    """
    metadata = {
        "@context" : "http://schema.org",
        "@type" : "DataFeed",
        "@id": str(req.url),
        "name": title,
        "alternateName": alt_name,
        "description": description,
        "isPartOf": {
            "@type": "WebPage",
            "url": str(req.url_for("root")) + locale_in_path(req) + "/" + params['token'],
            "datePublished": params['created'].isoformat()
        },
        "inLanguage" : {"@type" : "Language",
            'name': locale_names[current_locale.get().language]["en"],
            "alternateName": current_locale.get().language},
        "dateCreated": params['created'].isoformat(),
        "image": str(req.url_for("root")) + "static/logo.jpg",
        "url" : str(req.url)
    }
    
    return metadata

async def select_locale_by_force(req):
    """
    Override negotiated locale with the one in URL.
    """
    locale_str = locale_in_path(req)
    if locale_str != "":
        if locale_str in available_locales:
            locale = Locale.parse(locale_str, sep="-")
            token = current_locale.set(locale)
            return locale

    locale = await select_locale_by_request(req)
    if locale in available_locales:
        return locale
    
    return None

def locale_in_path(req):
    """
    Detect locale specifiec in URL.
    """
    path_len = len(req.url.path)
    if path_len == 3 or path_len > 3 and req.url.path[3] == '/':
        locale_str = req.url.path[1:3]
        if locale_str in available_locales:
            return locale_str
            
    return ""

async def serve_i18n_javacript(req):
    """
    Since Javascript i18n is always painful, just cut the Gordian knot with serving scripts as translatable templates.
    """
    accepted = ['collect.js', 'index.js', 'audit.js']

    filename = req.path_params['filename']
    
    if filename not in accepted:
        return

    return templates.TemplateResponse(filename, {'request': req, 'locale': current_locale.get().language}, media_type="text/javascript")
  
async def get_app_stats(req):
    """
    Display some general information about system status at `/api/stats`.
    """
    status = {}
    status["timestamp"] = datetime_representation()
    status['eventsource'] = {"creators": len(app.creators)}
    status['eventsource']['sessions'] = len(app.sessions)
    status['eventsource']['queues'] = sum(len(c['queue']) for c in app.sessions.values())
    status['eventsource']["events"] = sum(x.qsize() for c in app.sessions.values() for x in c['queue'])
    status["db_connections"] = {"pool_size": app.state.pool.get_size(),  "idle": app.state.pool.get_idle_size(), "active": app.state.pool.get_size()}

    return JSONResponse(status)
    
async def startup():
    """
    Initialize app.
    """
    logging.info(f"Starting Pseudovote {__version__} from {LASTMODIFIED}")
    
    app.sessions = {}
    app.users = {}
    app.creators = {}
    
    logging.info(f"Bulletin board public key \"{public_key_id}\" and private key \"{private_key_id}\".")
    
    app.state.pool = await asyncpg.create_pool(user="pseudo", password="default", host=DB_HOST, database="pseudovote", min_size = 2, max_size = 10)
    logging.info(f"Connection pool of {app.state.pool.get_size()}/{app.state.pool.get_max_size()} created.")
    app.state.notify_connection = await app.state.pool.acquire()
    await app.state.notify_connection.add_listener(notify_channel, database_listener) # maybe add_termination_listener
    logging.info(f"Connected to notify channel \"{notify_channel}\".")
    logging.info(f"Expecting SMTP server at \"{EMAIL_HOST}:{EMAIL_PORT}\".")
    
async def shutdown():
    """
    Shut everything down as gracefully as possible.
    """
    while len(app.sessions) > 0 or len(app.creators) > 0:
        logging.info(f"Waiting to disconnect {len(app.creators)} creators and {len(app.sessions)} channels with {sum(len(app.sessions[c]['queue']) for c in app.sessions)} clients.")
        await asyncio.sleep(1)
    logging.info(f"Pool has {app.state.pool.get_idle_size()} idle and {app.state.pool.get_size()} active connections.")
    logging.info(f"Closing {len(app.sessions)} channels with {sum(len(app.sessions[c]['queue']) for c in app.sessions)} queues.")
    await app.state.notify_connection.remove_listener(notify_channel, database_listener)
    await app.state.notify_connection.close()
    await app.state.pool.release(app.state.notify_connection)
    logging.info(f"Notify channel released.")
    await app.state.pool.close()
    logging.info("Connection pool closed.")

logger = logging.getLogger()
logger.setLevel(logging.INFO)

random = SystemRandom()

load_dotenv()

DB_HOST = getenv("DB_HOST", "localhost")
LOCALHOSTS = ["mail", "localhost", "127.0.0.1", "0.0.0.0"]

EMAIL_PORT = int(getenv("EMAIL_PORT", "25"))
EMAIL_HOST = getenv("EMAIL_HOST", "localhost")
EMAIL_USERNAME = getenv("EMAIL_USERNAME", "pseudo")
EMAIL_PASSWORD = getenv("EMAIL_PASSWORD", "default")

EMAIL_NOLIMIT = bool(getenv("EMAIL_NOLIMIT", "false").lower()[0] not in ("f", "0"))
EMAIL_LIMIT = int(getenv("EMAIL_LIMIT", "101"))
EMAIL_CHUNK = int(getenv("EMAIL_CHUNK", "10"))

flowers = read_lines("wordlists/flowers.txt")
islands = read_lines("wordlists/islands.txt")
emojis = read_lines("wordlists/emoji-animals.txt")
starwars = read_lines("wordlists/starwars.txt")
candidates = read_lines("wordlists/bands_named_after_persons.txt")

public_key = read_lines("keys/gpg-public.txt")
private_key = read_lines("keys/gpg-private.txt")
packets = list(AsciiData('\n'.join(public_key).encode('ascii')).packets())
public_key_id = packets[0].key_id.decode()
packets = list(AsciiData('\n'.join(private_key).encode('ascii')).packets())
private_key_id = packets[0].key_id.decode()

notify_channel = 'votes_and_bulletins'

available_locales = [l for l in next(walk("locales"), ([],[],[]))[1] if not l.startswith("__") and not l.endswith("__")]

locale_names = {}

for l in available_locales:
    c = locale_names[l] = {}
    p = Locale.parse(l)
    for n in available_locales:
        c[n] = p.get_language_name(n)

functions = {
    "_": gettext,
    "available_locales": available_locales,
    "locale_names": locale_names,
    "locale_in_path": locale_in_path,
    "version": __version__,
    "modified": LASTMODIFIED,
    "modified_date": LASTMODIFIED_DATE,
    "json_metadata": get_json_metadata
}

templates = Jinja2Templates(directory='templates')
templates.env.trim_blocks=True
templates.env.policies['json.dumps_kwargs'] = {'ensure_ascii': False}
templates.env.globals.update(functions)

routes = [
    Route('/', distributor_home, name="root"),

    Route('/sitemap.xml', sitemap),
    Route('/robots.txt', robots),    
    Mount('/static', app=StaticFiles(directory='static')),
    
    Route('/api/stats', get_app_stats),

    Route('/api/token', get_bulletin_token, methods=["POST"]),
    Route('/api/name', get_bulletin_name, methods=["POST"]),
    
    Route('/api/vote', process_vote, methods=["POST"]),
    Route('/api/bulletin/{token}', subscribe_bulletin, methods=["GET"]),
    
    Route('/dynamic/{filename}', serve_i18n_javacript),
    Route('/audit/{token}', audit_bulletin),
]

locale_sub_routes = [
    Route('/', distributor_home, name="root"),
    Route('/dynamic/{filename}', serve_i18n_javacript),
    Route('/audit/{token}', audit_bulletin),
    
    Route('/{token}/{pseudonym}', collector),
    Route('/{token}', collector),
]

for locale in available_locales:
    routes.append(Route('/'+locale, distributor_home, name='locale'))
    routes.append(Mount('/'+locale, routes=locale_sub_routes, name=locale))

routes_catchall = [
    Route('/{token}/{pseudonym}', collector),
    Route('/{token}', collector),
]

routes.extend(routes_catchall)

middleware = [
    Middleware(
        BabelMiddleware,
        locale_selector=select_locale_by_force
    ),
]

app = Starlette(on_startup=[startup], on_shutdown=[shutdown], routes=routes, middleware=middleware, debug=True)
