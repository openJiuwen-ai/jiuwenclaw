from jiuwenswarm.gateway.channel_manager.im_platforms.wechat.wechat_connect import (
    build_wechat_qr_display,
)
from jiuwenswarm.gateway.channel_manager.im_platforms.wechat.qr_delivery import (
    _write_qr_image,
    build_wechat_login_qr_delivery,
    wechat_delivery_payload,
)


def test_weixin_hosted_qr_page_url_is_returned_as_encode_payload():
    url = "https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=abc&bot_type=3"

    qr = build_wechat_qr_display({"qrcode_img_content": url, "qrcode": "abc"})

    assert qr is not None
    assert qr["kind"] == "encode"
    assert qr["value"] == url


def test_delivery_payload_is_structured_without_matching_model_text(tmp_path, monkeypatch):
    monkeypatch.setenv("JIUWENSWARM_WECHAT_QR_DIR", str(tmp_path))
    delivery = build_wechat_login_qr_delivery({
        "phase": "awaiting_scan",
        "qr": {"kind": "encode", "value": "https://example.test/current-qr"},
    })

    payload = wechat_delivery_payload(delivery)

    assert payload["source"] == "wechat_login"
    assert {item["kind"] for item in payload["artifacts"]} == {"image", "link"}


def test_write_qr_image_generates_png_for_encode_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("JIUWENSWARM_WECHAT_QR_DIR", str(tmp_path))

    path = _write_qr_image({"kind": "encode", "value": "https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=abc"})

    assert path is not None
    assert path.endswith(".png")
    assert (tmp_path / path.split("/")[-1]).exists()
