"""Guard the mobile rules; real layout is checked by the bounded browser fixture."""

from pathlib import Path


def test_mobile_pad_has_own_flow_row_and_content_sized_tiles():
    root = Path(__file__).resolve().parents[1]
    css = (root / "frontend/style.css").read_text()
    mobile = css.split("@media (max-width: 760px) {", 1)[1].split("@media (max-width: 480px)", 1)[0]
    assert "grid-auto-rows: max-content !important" in mobile
    assert "grid-template-rows: none !important" in mobile
    assert ".ptz-pad { position: relative; left: auto; bottom: auto;" in mobile
    assert ".ptz-slot { display: flex; flex: 0 0 100%;" in mobile
    assert "min-width: 44px; min-height: 44px" in mobile
    assert "aspect-ratio: 16 / 9" in mobile
    assert "#stage.single #players .cam.selected { height: auto; }" in mobile
    live = (root / "frontend/modules/live-cameras.js").read_text()
    assert 'className: "ptz-slot"' in live
