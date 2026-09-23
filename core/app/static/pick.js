(function(){
if (window.__laylaPick) return; window.__laylaPick = true;
var on = false, box = null, tag = null, current = null;
function ensure(){ if (box) return;
  box = document.createElement('div');
  box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #c18ada;background:rgba(193,138,218,.14);border-radius:4px;display:none;box-sizing:border-box';
  tag = document.createElement('div');
  tag.style.cssText = 'position:absolute;left:-2px;font:600 11px/18px system-ui,sans-serif;background:#c18ada;color:#1d1923;padding:0 6px;border-radius:4px;white-space:nowrap';
  box.appendChild(tag); document.documentElement.appendChild(box); }
function name(el){ var s = el.tagName.toLowerCase(); if (el.id) return s + '#' + el.id;
  var c = [].slice.call(el.classList, 0, 2); return c.length ? s + '.' + c.join('.') : s; }
function selector(el){ var parts = [];
  while (el && el.nodeType === 1 && el !== document.body && el !== document.documentElement && parts.length < 6) {
    if (el.id) { parts.unshift(el.tagName.toLowerCase() + '#' + el.id); break; }
    var part = name(el), parent = el.parentElement;
    if (parent) { var same = [].filter.call(parent.children, function(c){ return c.tagName === el.tagName; });
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(el) + 1) + ')'; }
    parts.unshift(part); el = parent; }
  return parts.join(' > '); }
function show(el){ ensure(); var r = el.getBoundingClientRect();
  box.style.display = 'block'; box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
  box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
  tag.textContent = name(el); tag.style.top = r.top < 24 ? (r.height + 2) + 'px' : '-22px'; }
function hide(){ if (box) box.style.display = 'none'; }
function pickable(el){ return el && el.nodeType === 1 && el !== document.body && el !== document.documentElement && !(box && box.contains(el)); }
document.addEventListener('mousemove', function(e){ if (!on) return;
  if (pickable(e.target)) { current = e.target; show(current); } else hide(); }, true);
document.addEventListener('click', function(e){ if (!on) return; e.preventDefault(); e.stopPropagation();
  var el = pickable(e.target) ? e.target : current; if (!pickable(el)) return; show(el);
  var html = el.outerHTML || ''; if (html.length > 2000) html = html.slice(0, 2000) + '…';
  parent.postMessage({ type: 'layla-picked', selector: selector(el), name: name(el), html: html,
    text: (el.innerText || '').trim().slice(0, 300) }, '*'); }, true);
document.addEventListener('keydown', function(e){ if (on && e.key === 'Escape') parent.postMessage({ type: 'layla-pick-cancel' }, '*'); }, true);
window.addEventListener('scroll', function(){ if (on && current) show(current); }, true);
window.addEventListener('message', function(e){ var d = e.data;
  if (d && d.type === 'layla-pick') { on = !!d.on; document.documentElement.style.cursor = on ? 'crosshair' : '';
    if (!on) { hide(); current = null; } } });
})();
