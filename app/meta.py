from __init__ import __version__, LASTMODIFIED
from dateutil.parser import parse
from datetime import datetime
from i18n import locale_names
from asgi_babel import current_locale
from i18n import locale_in_path

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
