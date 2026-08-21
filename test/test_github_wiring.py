"""Source-level gates for the optional GitHub module."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text()


class GitHubWiringTests(unittest.TestCase):
    def test_fresh_clone_keeps_both_features_off(self):
        config = read("components/app_tokens/app_tokens_config.h")
        self.assertIn("#define TK_GITHUB_SCREEN_ENABLED 0", config)
        self.assertIn("#define TK_GITHUB_NOTIFICATIONS_ENABLED 0", config)
        self.assertIn("#define TK_GITHUB_SOUND_ENABLED 0", config)

    def test_screen_and_popup_are_independent_switches(self):
        config = read("components/app_tokens/app_tokens_config.h")
        screen = config.index("TK_GITHUB_SCREEN_ENABLED")
        notification = config.index("TK_GITHUB_NOTIFICATIONS_ENABLED")
        self.assertNotEqual(screen, notification)
        net = read("components/app_tokens/github_net.c")
        self.assertIn(
            "(TK_GITHUB_SCREEN_ENABLED || TK_GITHUB_NOTIFICATIONS_ENABLED)",
            net)

    def test_github_is_one_optional_seventh_view(self):
        header = read("components/app_tokens/usage_screen.h")
        app = read("components/app_tokens/app_tokens.h")
        ui = read("components/app_tokens/usage_screen.c")
        # Six base tiles + the optional GitHub tile + the always-present Value
        # tile: GitHub stays at index 6, Value is the new last tile at 7.
        self.assertIn("(6 + TK_GITHUB_SCREEN_ENABLED + 1)", header)
        self.assertIn("VIEW_GITHUB = 6", app)
        self.assertIn("VIEW_VALUE = 7", app)
        self.assertIn("set_star_hero", ui)
        self.assertIn('"FORKS"', ui)
        self.assertNotIn("ISSUES", ui)
        self.assertNotIn("COMMITS", ui)
        style = read("components/app_tokens/project_star_style.h")
        self.assertIn("TK_PROJECT_STAR_COLOR_HEX 0xF2B84B", style)
        self.assertIn("TK_PROJECT_STAR_COLOR_HEX", ui)

    def test_popup_is_app_local_static_and_below_agent_attention(self):
        ui = read("components/app_tokens/usage_screen.c")
        popup = read("components/app_tokens/project_star_popup.c")
        self.assertLess(ui.index("tk_project_star_popup_create(root)"),
                        ui.index("tk_agent_monitor_create(root)"))
        self.assertNotIn("lv_layer_top", popup)
        self.assertNotIn("lv_anim", popup)
        self.assertIn("LV_OPA_COVER", popup)
        self.assertIn("lv_draw_triangle", popup)
        self.assertNotIn("image_recolor", popup)
        self.assertIn("filled_star(popup.root, 130, 80, 220, 220)", popup)
        self.assertIn("event->repo[0] ? event->repo : event->project", popup)
        self.assertIn('"%ld stars"', popup)
        self.assertNotIn("STARRED %s", popup)
        self.assertNotIn("NEW STAR", popup)
        self.assertIn("TAP TO DISMISS", popup)
        policy = read("components/app_tokens/project_star_popup_policy.h")
        self.assertIn("120LL * 1000000LL", policy)

    def test_simulation_has_event_key_and_exact_return_capture(self):
        sim = read("sim/main.c")
        self.assertIn("SDL_SCANCODE_G", sim)
        self.assertIn('"github-star.json"', sim)
        self.assertIn('"vibepulse-github-popup-before"', sim)
        self.assertIn('"vibepulse-github-popup-return"', sim)

    def test_device_never_contacts_github_public_api(self):
        device_net = read("components/app_tokens/github_net.c")
        server = read("tools/tokenserver/github_monitor.py")
        self.assertNotIn("api.github.com", device_net)
        self.assertIn("api.github.com", server)
        self.assertIn("FAILURE_BACKOFF_SECONDS", server)

    def test_chime_is_optional_and_renderer_independent(self):
        config = read("components/app_tokens/app_tokens_config.h")
        ui = read("components/app_tokens/usage_screen.c")
        cmake = read("components/app_tokens/CMakeLists.txt")
        popup = read("components/app_tokens/project_star_popup.c")
        chime = read("components/app_tokens/project_star_chime.c")
        self.assertIn("TK_GITHUB_SOUND_ENABLED", config)
        self.assertIn("project_star_chime.c", cmake)
        self.assertIn("tk_project_star_chime_request", ui)
        self.assertLess(ui.index("tk_project_star_popup_show(&event"),
                        ui.index("tk_project_star_chime_request()"))
        self.assertNotIn("chime", popup.lower())
        self.assertIn("880", chime)
        self.assertIn("1109", chime)
        self.assertNotIn("bsp_audio", chime)
        self.assertNotIn("esp_codec", chime)
        self.assertNotIn("i2s_", chime)

    def test_ci_compiles_the_default_off_optional_path(self):
        workflow = read(".github/workflows/ci.yml")
        self.assertIn("TK_GITHUB_SCREEN_ENABLED 1", workflow)
        self.assertIn("TK_GITHUB_NOTIFICATIONS_ENABLED 1", workflow)
        self.assertIn("TK_GITHUB_SOUND_ENABLED 1", workflow)
        # CI's tokenserver module list lives in the shared suite file.
        self.assertIn("test/tokenserver-suite.txt", workflow)
        self.assertIn("tools.tokenserver.test_github_monitor",
                      read("test/tokenserver-suite.txt"))


if __name__ == "__main__":
    unittest.main()
