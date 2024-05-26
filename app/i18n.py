from os import walk
from babel import Locale
from asgi_babel import current_locale, select_locale_by_request

available_locales = [l for l in next(walk("locales"), ([],[],[]))[1] if not l.startswith("__") and not l.endswith("__")]
locale_names = {}

for l in available_locales:
    c = locale_names[l] = {}
    p = Locale.parse(l)
    for n in available_locales:
        c[n] = p.get_language_name(n)

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
