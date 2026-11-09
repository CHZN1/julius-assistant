import re, unittest
from pathlib import Path

UI = Path(__file__).resolve().parent / "ui"
PROTO = (UI / "orb_prototype.html").read_text()
PAGE = (UI / "orb.html").read_text()


def between(s, a, b): return s[s.index(a):s.index(b)]


class ShellPage(unittest.TestCase):
    def test_page_is_up_to_date_with_the_build_script(self):
        import sys; sys.path.insert(0, str(UI))
        import build_shell
        self.assertEqual(build_shell.build.__module__, "build_shell")
        out = build_shell.OUT.read_text(); build_shell.build(); self.assertEqual(out, build_shell.OUT.read_text(), "run: python ui/build_shell.py")

    def test_render_code_is_the_prototypes_byte_for_byte(self):
        # shaders, grain generation, finishes, pointer physics, trail and brush handling
        for a, b in (("const vs = `#version 300 es", "let finish='opal'"), ("function axisAngle", "cv.addEventListener('pointerleave'"), ("const jar = {silk:0.8", "let W = 0;")):
            self.assertIn(between(PROTO, a, b), PAGE, a)
        self.assertIn(between(PROTO, "const TN = 16", "function axisAngle"), PAGE)
        self.assertIn("cv.addEventListener('pointerdown', shake);", PAGE)

    def test_frame_body_differs_only_by_the_named_patches(self):
        proto = between(PROTO, "function frame(ms){", "// UI\n"); page = between(PAGE, "function frame(ms){", "// UI\n")
        import difflib
        changed = [l for l in difflib.unified_diff(proto.splitlines(), page.splitlines(), lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        self.assertEqual(len([l for l in changed if l.startswith("-")]), 5, changed)   # target, E, breathe... five lines touched
        self.assertTrue(all(any(k in l for k in ("shell", "frame(ms)")) for l in changed if l.startswith("+")), changed)

    def test_approved_defaults_and_all_eight_finishes(self):
        self.assertIn("let finish='opal', state='idle', mode='pulsescatter', gsize=0, brushMode='flares';", PAGE)
        self.assertIn("const jar = {silk:0.8, swirl:0.6, visc:0.5, settle:0.2, glitter:0.2};", PAGE)
        self.assertIn('id="count" min="200" max="2400" step="50" value="2400"', PAGE)
        for name in ("Light gold", "Rose gold", "Metallic silver", "Emerald", "Amethyst", "Opal", "Obsidian", "Cobalt"): self.assertIn(name, PAGE)

    def test_every_element_the_script_uses_exists(self):
        ids = set(re.findall(r'id="([^"]+)"', PAGE.split("<script>")[0]))
        used = set(re.findall(r"getElementById\('([^']+)'\)", PAGE.split("<script>", 1)[1]))
        used |= {m for m in re.findall(r"group\('([^']+)'", PAGE)}
        for k in ("silk", "swirl", "visc", "settle", "glitter"): used.add(k + "Val")
        missing = {u for u in used if u not in ids and not u.endswith("Val")} | {u for u in used if u.endswith("Val") and u not in ids}
        self.assertEqual(missing, set())

    def test_states_and_bridge(self):
        for needle in ("quiet", "asleep", "Julius is offline", "window.julius", "webkit.messageHandlers.native", "shellAmp", "Math.min(+document.getElementById('count').value*1000, CAPS"): self.assertIn(needle, PAGE)
        self.assertIn("minimal: 400000", PAGE); self.assertIn("reduced: 800000", PAGE)

    def test_bubble_sits_below_the_orb_and_shows_only_while_speaking(self):
        self.assertIn("top:calc(var(--s) + 4px)", PAGE); self.assertIn("--s:min(100vw,calc(100vh / 1.24))", PAGE)
        self.assertIn("const BUBBLE_HOLD = 4000;", PAGE)
        self.assertNotIn("poke(60000)", PAGE); self.assertNotIn("poke(8000)", PAGE)
        self.assertIn("if(s === 'offline') document.body.classList.add('show-ui');", PAGE)

    def test_settings_drawer_can_be_closed_three_ways(self):
        self.assertIn('id="closeDrawer"', PAGE); self.assertIn("e.key === 'Escape'", PAGE)
        self.assertIn("e.target.closest('#drawer')", PAGE); self.assertIn("}, true);", PAGE)   # click outside, capture phase so the orb is not also shaken

    def test_no_external_requests(self):
        self.assertNotIn("fonts.googleapis", PAGE); self.assertNotRegex(PAGE, r"""(src|href)=["']https?://""")


if __name__ == "__main__":
    unittest.main()
