var votesIncoming = null;

function init_message_board() {
  votesIncoming = document.getElementById('votes');
  use_bottom_edge(votesIncoming);

  // last_vote must be defined before this function
  var source = new SSE(
    '/api/bulletin?last_vote_number=' + (last_vote_number ?? '')
  );

  function closeBulletinStream() {
    source.close();
    votesIncoming.value +=
      '=== DISCONNECTED: ' + new Date().toLocaleString() + ' ===\n';
    votesIncoming.value +=
      '\n{{ _("Thank you for taking digital democracy seriously!") }}\n\n';
    scrollVotesToEnd();
  }

  source.onmessage = function (evt) {
    const raw = evt.data;

    if (!raw.startsWith('{')) {
      //console.log(raw);
    } else {
      const json = JSON.parse(raw);
      if (!('state' in json && 'data' in json)) {
        status_message(JSON.stringify(json));
        return;
      }

      const data = json.data;

      const state = json.state;
      const STATE = state.toUpperCase();

      console.log(`${STATE} ${JSON.stringify(json.data)}`);

      if (state == 'end') {
        if (!bulletin_timing.ended) {
          let ended_time = new Date(data.timestamp);
          votesIncoming.value += `=== FINISHED: ${ended_time.toLocaleString()} ===\n`;
        }

        let grace = 5 * 60;
        console.log(`CLOSING ${source.url} in ${grace} seconds.`);
        bulletin_ended = true;
        setTimeout(closeBulletinStream, grace * 1000);
      } else if (state == 'wait-start') {
        setCountDown(
          '{{ _("Starting in") }} ',
          'cautious',
          '{{ _("Voting started!") }}',
          'lucky',
          data.timestamp
        );
      } else if (state == 'wait-end') {
        if (!bulletin_timing.started) {
          let start_time = new Date(data.started);
          votesIncoming.value += `=== STARTED: ${start_time.toLocaleString()} ===\n`;
        }

        setCountDown(
          '{{ _("Ending in") }} ',
          'lucky',
          '{{ _("Voting finished!") }}',
          'trapped',
          data.timestamp
        );
      } else if (state == 'incoming-vote') {
        votesIncoming.value += `${data.number}) ${data.pseudonym}: ${data.content}\n`;
        scrollVotesToEnd();
      } else if ('state' in data) {
        votesIncoming.value += `${STATE}: ${JSON.stringify(data)}\n`;
      } else {
        votesIncoming.value += `${JSON.stringify(data)}\n`;
      }
    }
  }; // onmessage

  source.onerror = function (e) {
    if (e.readyState == EventSource.CONNECTING) {
      if (!!e.prevReadyState) {
        votesIncoming.value +=
          '=== RECONNECTING: ' + new Date().toLocaleString() + ' ===\n';
        scrollVotesToEnd();
      }
    } else if (e.readyState == EventSource.OPEN) {
      votesIncoming.value +=
        '=== CONNECTED: ' + new Date().toLocaleString() + ' ===\n';
      scrollVotesToEnd();
    } else if (!bulletin_ended && e.readyState == EventSource.CLOSED) {
      votesIncoming.value +=
        '=== DISCONNECTED: ' + new Date().toLocaleString() + ' ===\n';
      scrollVotesToEnd();
    }
  }; // onerror
  source.stream();
  scrollVotesToEnd();
}

function scrollVotesToEnd() {
  if (!votesIncoming.contains(document.activeElement)) {
    votesIncoming.scrollTop = votesIncoming.scrollHeight;
  }
}
