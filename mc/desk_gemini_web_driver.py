"""The real browser driver for the Gemini web engine: `PaneDriver` plus the four reads
the engine needs that posting never did (current URL, a phrase check, a picture count, a
picture's bytes).

It drives a pane that is ALREADY OPEN on the named profile and nothing else
(`pane_driver_for`); it never launches a browser and never types into a sign-in page.
Everything it is told (selectors, host lists, needles) comes from
`mc/desk_gemini_web_selectors.py` through `json.dumps`; nothing the page says builds an
expression. The page can only answer with a boolean, a count, a URL string, or image bytes
that the caller checks by magic number.
"""
from __future__ import annotations

import base64
import json

from mc.desk_assisted_pane import PaneDriver

_VISIBLE_JS = ("function vis(el){var r=el.getBoundingClientRect();var s=getComputedStyle(el);"
               "return r.width>1&&r.height>1&&s.visibility!=='hidden'&&s.display!=='none';}")


def _images_js(css: str, min_px: int) -> str:
    # Loaded, visible pictures at least min_px on both sides, in document order.
    return (f"(function(){{{_VISIBLE_JS}var out=[];var n=document.querySelectorAll({json.dumps(css)});"
            "for(var i=0;i<n.length;i++){var e=n[i];"
            f"if(vis(e)&&e.complete&&e.naturalWidth>={int(min_px)}&&e.naturalHeight>={int(min_px)})out.push(e);}}"
            "return out;})()")


class GeminiPaneDriver(PaneDriver):
    def url(self) -> str:
        v = self._eval('location.href')
        return v if isinstance(v, str) else ''

    def visible(self, css: str) -> bool:
        return self.wait(css, 0.05)

    def page_has_text(self, scope_css: str, needles: list[str]) -> bool:
        """True when the text of the LAST element matching scope_css contains any needle.
        The text itself never leaves the page."""
        js = (f"(function(){{var n=document.querySelectorAll({json.dumps(scope_css)});"
              "if(!n.length)return false;var t=(n[n.length-1].innerText||'').toLowerCase();"
              f"var w={json.dumps([s.lower() for s in needles])};"
              "for(var i=0;i<w.length;i++){if(t.indexOf(w[i])>=0)return true;}return false;}})()")
        return bool(self._eval(js))

    def image_count(self, css: str, min_px: int) -> int:
        v = self._eval(f"{_images_js(css, min_px)}.length")
        return v if isinstance(v, int) else 0

    def fetch_image(self, css: str, index: int, min_px: int, rewrite: dict, hosts: list[str],
                    max_bytes: int, timeout: float) -> tuple[bytes, str] | None:
        """The `index`th result picture's bytes, read inside the page with its own session
        (the picture's URL, rewritten to ask for the original size). None when the page
        would not hand it over; the caller says so. A URL off `hosts` is refused here."""
        js = f"""(async function(){{
          var imgs={_images_js(css, min_px)}; var el=imgs[{int(index)}]; if(!el) return null;
          var u=el.currentSrc||el.src||''; if(!u) return null;
          var re=new RegExp({json.dumps(rewrite['pattern'])}); if(re.test(u)) u=u.replace(re,{json.dumps(rewrite['replace'])});
          var p; try{{p=new URL(u);}}catch(e){{return null;}}
          var hosts={json.dumps(list(hosts))};
          var ok=p.protocol==='blob:'||(p.protocol==='https:'&&hosts.some(function(h){{return p.hostname===h||p.hostname.endsWith('.'+h);}}));
          if(!ok) return {{refused:'host'}};
          var r=await fetch(u,{{credentials:'include'}}); if(!r.ok) return {{status:r.status}};
          var b=await r.blob(); if(b.size>{int(max_bytes)}) return {{refused:'size'}};
          var d=await new Promise(function(res,rej){{var f=new FileReader();f.onload=function(){{res(f.result);}};f.onerror=rej;f.readAsDataURL(b);}});
          return {{b64:String(d).split(',')[1]||'', mime:b.type||''}};
        }})()"""
        r = self._conn().call('Runtime.evaluate', {'expression': js, 'awaitPromise': True, 'returnByValue': True},
                              timeout=int(timeout))
        value = (r.get('result') or {}).get('value')
        if not isinstance(value, dict) or not isinstance(value.get('b64'), str) or not value['b64']:
            return None
        try:
            return base64.b64decode(value['b64'], validate=True), str(value.get('mime') or '')
        except (ValueError, TypeError):
            return None


def pane_driver_for(profile: str) -> GeminiPaneDriver:
    """The driver for the pane already open on `profile`; AssistedError if none is."""
    from mc.blueprints import browser_routes as br
    from mc.desk_assisted_post import AssistedError
    session = br._session_using_profile(profile)
    if not session:
        raise AssistedError(f"open the browser pane on profile '{profile}' and sign in to Gemini first",
                            'pane_not_open')
    return GeminiPaneDriver(session)
