import pytest

from cage import CageError
from cage import naming
from cage import state


def test_sanitize_name_collapses_truncates():
    assert naming.sanitize_name("my project!!!") == "my-project"
    assert len(naming.sanitize_name("x" * 45)) == 40


def test_source_slug_from_git_url():
    assert naming.source_slug("https://github.com/foo/my-agent.git") == "my-agent"


def test_image_tag_stable_and_distinct():
    a = naming.image_tag("tooled", "local", "img:1")
    assert a.startswith("cage-agent:tooled-") and len(a.split("-")[-1]) == 10
    assert a == naming.image_tag("tooled", "local", "img:1")          # 稳定
    assert a != naming.image_tag("strict", "local", "img:1")          # profile 异
    assert a != naming.image_tag("tooled", "local", "img:2")          # 基础镜像异（裁定 #11）


def test_allocate_port_skips_recorded_published_listening_and_exclude():
    port = naming.allocate_port({18080}, {18081}, {18082}, {state.SERVE_PORT})
    assert port == 18083
    with pytest.raises(CageError):
        naming.allocate_port(set(range(18080, 18120)), set(), set(), set())
