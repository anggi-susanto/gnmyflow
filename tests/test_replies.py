"""Reply readers, against synthetic payloads shaped like Flow's (captured 2026-10-10)."""

from __future__ import annotations

from studiowright.flow import replies as r

PROJECT = "00000000-0000-4000-8000-000000000001"
IMG = "6b571002-6626-49c6-83d8-af39056616fa"
IMG2 = "4cb814e3-22b6-4e2f-975a-208480554779"
WF = "7206ba83-5c0d-41e9-a4c1-d7b39057a6ae"
CLIP = "bd5e42da-d072-4f7c-8b6b-92f51df4fe6a"
CLIP_WF = "ba23d35a-90c4-44ad-aaa8-af602d338ebc"


def image_url(media_id: str) -> str:
    return f"https://flow-content.google/image/{media_id}?Expires=1&Signature=abc"


def image_tuple(media_id: str, size: list[int]) -> list:
    details = [
        None,
        116168830,
        None,
        None,
        None,
        None,
        1,
        "prompt",
        36,
        None,
        None,
        WF,
        None,
        image_url(media_id),
        3,
        [None, None, [["prompt", None, [[["prompt"]]]]], []],
        None,
        WF,
    ]
    return [media_id, None, WF, None, None, None, details, None, size]


def test_image_results_reads_ids_urls_and_sizes() -> None:
    payload = [
        [image_tuple(IMG, [1376, 768]), image_tuple(IMG2, [768, 1376])],
        [[WF, None, None, ["Green teapot", [1, 2]], PROJECT]],
    ]
    results = r.image_results(payload)
    assert [(x.media_id, x.width, x.height) for x in results] == [
        (IMG, 1376, 768),
        (IMG2, 768, 1376),
    ]
    assert results[0].url == image_url(IMG)


def test_image_results_empty_for_unrelated_payload() -> None:
    assert r.image_results([[None, 1, ["text"]]]) == []


def test_upload_result_reads_media_id_and_name() -> None:
    payload = [
        [
            IMG,
            PROJECT,
            WF,
            None,
            None,
            [[1, 2]],
            [None, [None, None, None, None, 0, None, "image/jpeg"], [64, 64]],
        ],
        [WF, None, None, ["probe-red.png", [1, 2], None, None, IMG], PROJECT],
    ]
    result = r.upload_result(payload)
    assert result.media_id == IMG
    assert result.name == "probe-red.png"


def test_upload_result_rejects_shape_without_id() -> None:
    try:
        r.upload_result([["not-an-id"]])
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def video_record(status: int, with_url: bool) -> list:
    details = [None] * 14
    details[8] = [status]
    media_info = [[None] * 13]
    if with_url:
        media_info[0][8] = f"https://flow-content.google/video/{CLIP}?Expires=1&Signature=x"
        details[10] = f"https://flow-content.google/image/{CLIP}?Expires=1&Signature=y"
    return [CLIP, PROJECT, CLIP_WF, None, None, details, None, media_info]


def test_video_records_track_status_and_urls() -> None:
    queued = r.video_records([None, 120, [[CLIP, PROJECT]], [[video_record(6, False)]]])
    assert [(v.media_id, v.status, v.video_url) for v in queued] == [(CLIP, 6, None)]
    done = r.video_records([[video_record(3, True)]], project_id=PROJECT)
    assert done[0].status == r.STATUS_DONE
    assert done[0].video_url and done[0].video_url.startswith(r.VIDEO_URL_PREFIX)
    assert done[0].workflow_id == CLIP_WF


def test_video_records_filter_by_project() -> None:
    assert (
        r.video_records(
            [[video_record(3, True)]], project_id="00000000-0000-0000-0000-000000000000"
        )
        == []
    )


def test_media_urls_groups_by_media_id() -> None:
    payload = [
        [
            f"https://flow-content.google/video/{CLIP}?a=1",
            f"https://flow-content.google/image/{CLIP}?b=2",
            image_url(IMG),
        ]
    ]
    urls = r.media_urls(payload)
    assert set(urls[CLIP]) == {"video", "image"}
    assert set(urls[IMG]) == {"image"}


def test_image_results_carry_workflow_ids() -> None:
    payload = [[image_tuple(IMG, [1376, 768])]]
    assert r.image_results(payload)[0].workflow_id == WF


def test_workflow_ids_from_records() -> None:
    assert r.workflow_ids([[video_record(3, True)]]) == {CLIP: CLIP_WF}


def test_credit_balance_from_a_video_submit_reply() -> None:
    from studiowright.flow.replies import credit_balance

    assert credit_balance([None, 427, [["media", "project"]], [["record"]]]) == 427
    assert credit_balance([None, True, []]) is None
    assert credit_balance(["x", 5, []]) is None
    assert credit_balance({"credits": 5}) is None
