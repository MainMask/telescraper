"""Offline tests for the pure helpers (no Telegram network, no credentials)."""

import json

import pandas as pd
import pytest

from telescraper.analysis import (
    _TME_BASE_RE, _TME_RE, _count_comments, _sibling_reactors, combine,
    explode_comments, filter_keywords, links, participants, sample,
)
from telescraper.cli import build_parser
from telescraper.datafiles import clean_xml_text, format_duration, read_table, save_table
from telescraper.scrape import _channel_ref, _progress_bar, channel_slug, parse_date


def test_clean_xml_text_handles_none_and_control_chars():
    assert clean_xml_text(None) == ""
    assert clean_xml_text("a\x00b\x07c") == "abc"
    assert clean_xml_text("normal текст 😀") == "normal текст 😀"


def test_format_duration():
    assert format_duration(90061) == "01:01:01:01"


def test_progress_bar():
    assert _progress_bar(0) == "░" * 20
    assert _progress_bar(1) == "█" * 20
    assert _progress_bar(0.5).count("█") == 10
    assert _progress_bar(-1) == "░" * 20        # clamps below 0
    assert _progress_bar(2) == "█" * 20         # clamps above 1
    assert len(_progress_bar(0.37)) == 20


@pytest.mark.parametrize("fmt", ["parquet", "xlsx", "csv"])
def test_save_read_roundtrip(tmp_path, fmt):
    df = pd.DataFrame({"Group": ["@a", "@b"], "Content": ["hi", "yo"]})
    path = save_table(df, tmp_path / "out", fmt)
    back = read_table(path)
    assert list(back["Content"]) == ["hi", "yo"]


def test_parse_date_end_of_day_is_utc():
    d = parse_date("2025-01-15", end_of_day=True)
    assert (d.hour, d.minute, d.second) == (23, 59, 59)
    assert d.tzinfo is not None


def test_parse_date_accepts_dotted_and_iso():
    dotted = parse_date("10.07.2015")
    assert (dotted.year, dotted.month, dotted.day) == (2015, 7, 10)
    assert dotted == parse_date("2015-07-10")
    assert parse_date("10.07.2015", end_of_day=True).hour == 23


def test_parse_date_rejects_garbage():
    with pytest.raises(SystemExit):
        parse_date("not-a-date")


def test_count_comments_from_json_string():
    payload = '[{"Type": "comment"}, {"Type": "comment"}, {"Type": "text"}]'
    assert _count_comments(payload) == 2
    assert _count_comments(None) == 0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@durov", "durov"),
        ("durov", "durov"),
        ("https://t.me/durov", "durov"),
        ("http://t.me/durov/", "durov"),
        ("t.me/durov/123?comment=1", "durov"),
        ("https://t.me/+AbCdEf", "+AbCdEf"),
        ("  https://telegram.me/durov  ", "durov"),
        ("https://t.me/s/durov", "durov"),               # web preview
        ("https://t.me/joinchat/AbC", "+AbC"),           # legacy invite = t.me/+AbC
    ],
)
def test_channel_slug(raw, expected):
    assert channel_slug(raw) == expected


@pytest.mark.parametrize(
    "raw,arg,slug,url_base",
    [
        ("@durov", "@durov", "durov", "https://t.me/durov"),
        ("https://t.me/durov/9", "https://t.me/durov/9", "durov", "https://t.me/durov"),
        ("-1001629147115", -1001629147115, "c1629147115", "https://t.me/c/1629147115"),
        ("1629147115", 1629147115, "c1629147115", "https://t.me/c/1629147115"),
        ("https://t.me/s/durov", "@durov", "durov", "https://t.me/durov"),
        ("https://t.me/joinchat/AbC", "https://t.me/+AbC", "+AbC", "https://t.me/+AbC"),
        ("+AbCd", "https://t.me/+AbCd", "+AbCd", "https://t.me/+AbCd"),
        ("@+AbCd", "https://t.me/+AbCd", "+AbCd", "https://t.me/+AbCd"),  # menu guess from Group
    ],
)
def test_channel_ref(raw, arg, slug, url_base):
    ref = _channel_ref(raw)
    assert (ref.arg, ref.slug, ref.url_base) == (arg, slug, url_base)


def test_save_table_keeps_dotted_name(tmp_path):
    df = pd.DataFrame({"a": [1]})
    out = save_table(df, tmp_path / "my.data.2024", "parquet")
    assert out.name == "my.data.2024.parquet"
    assert read_table(out)["a"].tolist() == [1]


def test_combine_errors_on_empty_inputs(tmp_path):
    pd.DataFrame().to_parquet(tmp_path / "empty.parquet")
    with pytest.raises(SystemExit):
        combine(str(tmp_path / "*.parquet"), str(tmp_path / "out.parquet"), ["Group", "Message ID"])


def _posts_with_comments(tmp_path, name="posts.parquet"):
    comment = {
        "Type": "comment", "Comment Author ID": 5, "Comment Author Username": "bob",
        "Comment Author Name": "Bob B", "Comment Content": "hi",
        "Comment Date": "2025-01-01 00:00:00", "Comment Message ID": 100,
        "Comment Author": None, "Comment Views": None, "Comment Reactions": "",
        "Comment Shares": 0, "Comment Media": False,
        "Comment Url": "https://t.me/c/1/10?comment=100",
    }
    anon = {"Type": "comment", "Comment Author ID": None,
            "Comment Author Username": "[anonymous]", "Comment Author Name": ""}
    df = pd.DataFrame({
        "Group": ["@c1", "@c1"],
        "Message ID": [10, 11],
        "Url": ["https://t.me/c/1/10", "https://t.me/c/1/11"],
        "Comments List": [json.dumps([comment, anon]), "[]"],
    })
    src = tmp_path / name
    df.to_parquet(src)
    return src


def test_explode_comments(tmp_path):
    out = tmp_path / "comments.parquet"
    explode_comments(str(_posts_with_comments(tmp_path)), str(out))
    r = read_table(out)
    assert len(r) == 2
    assert "Comment Author Name" in r.columns
    row = r[r["Comment Author ID"] == 5].iloc[0]
    assert row["Comment Author Username"] == "bob"
    assert row["Comment Author Name"] == "Bob B"
    assert row["Post ID"] == 10
    assert row["Post Url"] == "https://t.me/c/1/10"


def test_explode_comments_errors_when_empty(tmp_path):
    pd.DataFrame({"Group": ["@c1"], "Message ID": [10], "Comments List": ["[]"]}).to_parquet(
        tmp_path / "posts.parquet"
    )
    with pytest.raises(SystemExit):
        explode_comments(str(tmp_path / "posts.parquet"), str(tmp_path / "out.parquet"))


def test_participants_merges_commenters_and_reactors(tmp_path):
    src = _posts_with_comments(tmp_path, "x_posts.parquet")
    pd.DataFrame({
        "Reactor ID": [5, 5, 9, -1001490082514],
        "Reactor Username": ["", "", "ann", "[channel]"],
        "Reactor Name": ["Bob B", "Bob B", "Ann A", "Some Chan"],
        "Reaction": ["👍", "🔥", "👍", "❤"],
    }).to_parquet(tmp_path / "x_reactors.parquet")
    (tmp_path / "other_reactors.parquet").write_bytes(b"unrelated")  # must NOT be picked up

    out = tmp_path / "people.parquet"
    participants(str(src), str(out))
    p = read_table(out).set_index("ID")

    assert set(p.index) == {5, 9}  # anonymous comment (ID None) + channel (negative ID) dropped
    assert list(read_table(out).columns) == ["ID", "Username", "Name", "Comments", "Reactions", "Total"]
    assert (p.loc[5, "Comments"], p.loc[5, "Reactions"], p.loc[5, "Total"]) == (1, 2, 3)
    assert p.loc[5, "Username"] == "bob"          # from the comment
    assert p.loc[5, "Name"] == "Bob B"
    assert (p.loc[9, "Comments"], p.loc[9, "Reactions"]) == (0, 1)
    assert p.loc[9, "Name"] == "Ann A"


def test_sibling_reactors_matches_dated_names(tmp_path):
    span = "_01.05.2022-24.07.2026"
    (tmp_path / f"Baza_reactors{span}.parquet").write_bytes(b"x")
    posts = tmp_path / f"Baza_posts{span}.parquet"
    posts.write_bytes(b"x")
    assert _sibling_reactors(str(posts)) == [tmp_path / f"Baza_reactors{span}.parquet"]

    # undated (pre-feature) layout still resolves
    (tmp_path / "Old_reactors.parquet").write_bytes(b"x")
    assert _sibling_reactors(str(tmp_path / "Old_posts.parquet")) == [tmp_path / "Old_reactors.parquet"]


def test_participants_errors_when_nobody(tmp_path):
    pd.DataFrame({"Comments List": ["[]"]}).to_parquet(tmp_path / "posts.parquet")
    with pytest.raises(SystemExit):
        participants(str(tmp_path / "posts.parquet"), str(tmp_path / "out.parquet"))


def test_combine_custom_dedup_cols_without_message_id(tmp_path):
    pd.DataFrame(
        {"Url": ["a", "b", "a"], "Date": ["2024-01-01", "2024-01-02", "2024-01-01"],
         "Comments List": [None, None, None]}
    ).to_parquet(tmp_path / "f.parquet")
    out = tmp_path / "out.parquet"
    combine(str(tmp_path / "*.parquet"), str(out), ["Url"])
    assert len(read_table(out)) == 2


def test_scrape_rejects_negative_timeout():
    argv = ["scrape", "--channels", "@x", "--date-min", "2024-01-01",
            "--date-max", "2024-01-02", "--name", "t", "--timeout", "-1"]
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_tme_link_extraction_and_normalisation():
    text = "join https://t.me/foo/123 and https://t.me/bar?x=1"
    links = _TME_RE.findall(text)
    assert len(links) == 2
    assert _TME_BASE_RE.match(links[0]).group(1) == "foo"


def test_links_keep_channel_for_web_private_and_invite_links(tmp_path):
    text = ("https://t.me/s/durov https://t.me/durov/5 https://t.me/boost/durov "
            "https://t.me/c/123/5 https://t.me/joinchat/AbC https://t.me/addlist/XyZ "
            "https://t.me/foo/123 https://t.me/share/url?url=x https://t.me/addstickers/Pack "
            "https://t.me/iv?url=x https://t.me/boost t.me/foo abct.me/nope "
            "t.me/DUROV https://t.me/+AbCd https://t.me/+abcd https://t.me/+AbC")
    pd.DataFrame({"Content": [text]}).to_parquet(tmp_path / "in.parquet")
    links(str(tmp_path / "in.parquet"), str(tmp_path / "l"))
    counts = dict(read_table(tmp_path / "l.xlsx").values)
    assert counts == {"https://t.me/durov": 4, "https://t.me/+AbCd": 1, "https://t.me/+abcd": 1, "https://t.me/c/123": 1,
                      "https://t.me/+AbC": 2,  # joinchat/AbC is the same invite
                      "https://t.me/addlist/XyZ": 1,
                      "https://t.me/foo": 2}


@pytest.mark.parametrize("n_matches, files", [(3, ["f_unique.xlsx"]),
                                              (4, ["f_part_1.xlsx", "f_part_2.xlsx"])])
def test_filter_file_split(tmp_path, n_matches, files):
    pd.DataFrame({"Content": ["foo"] * n_matches}).to_parquet(tmp_path / "in.parquet")
    filter_keywords(str(tmp_path / "in.parquet"), str(tmp_path / "f"), "Content", ["foo"], 3)
    assert sorted(p.name for p in tmp_path.glob("f_*.xlsx")) == files


def test_save_table_creates_parent_dir(tmp_path):
    path = save_table(pd.DataFrame({"a": [1]}), tmp_path / "new" / "x", "parquet")
    assert path == tmp_path / "new" / "x.parquet" and path.exists()


def test_parse_date_converts_offset_to_utc():
    assert parse_date("2024-01-01T03:00:00+03:00") == parse_date("2024-01-01")


def test_read_table_rejects_xls(tmp_path):
    with pytest.raises(ValueError, match="Unsupported file type"):
        read_table(tmp_path / "old.xls")


def test_sample_larger_than_data_returns_all_rows(tmp_path, capsys):
    # URL-only texts pass the length filter but are empty once URLs are stripped
    texts = ([f"long enough text number {i} here" for i in range(46)]
             + [f"https://example.com/some/long/url/{i}" for i in range(4)])
    pd.DataFrame({"id": range(50), "Content": texts, "Group": ["@a"] * 30 + ["@b"] * 20}
                 ).to_parquet(tmp_path / "in.parquet")
    sample(str(tmp_path / "in.parquet"), str(tmp_path / "s"), "Content", "Group", 10_000, 20)
    out = read_table(tmp_path / "s.xlsx")
    assert sorted(out["id"]) == list(range(50))  # every source row exactly once
    assert f"Saved: {tmp_path / 's.xlsx'} (50 rows)" in capsys.readouterr().out


def test_participants_skips_excel_truncated_comments_list(tmp_path, capsys):
    long_thread = json.dumps([{"Type": "comment", "Comment Author ID": i,
                               "Comment Content": "Привет мир " * 30} for i in range(120)])
    ok_thread = json.dumps([{"Type": "comment", "Comment Author ID": 5,
                             "Comment Author Username": "bob"}])
    df = pd.DataFrame({"Group": ["@a", "@a"], "Message ID": [1, 2],
                       "Comments List": [long_thread, ok_thread]})
    with pytest.warns(UserWarning):  # openpyxl truncates the long cell to 32767 chars
        src = save_table(df, tmp_path / "x_posts", "excel")

    participants(str(src), str(tmp_path / "people.parquet"), reactors="")
    assert list(read_table(tmp_path / "people.parquet")["ID"]) == [5]
    assert "@a/1: Comments List is not valid JSON" in capsys.readouterr().out


def test_sample_and_filter_keep_excel_truncated_comments_list(tmp_path):
    long_thread = json.dumps([{"Type": "comment", "Comment Content": "Привет мир " * 30}] * 120)
    df = pd.DataFrame({"Group": ["@a"], "Content": ["some long enough content here"],
                       "Comments List": [long_thread]})
    with pytest.warns(UserWarning):  # openpyxl truncates the long cell to 32767 chars
        src = str(save_table(df, tmp_path / "x_posts", "excel"))

    sample(src, str(tmp_path / "s"), "Content", "Group", 10, 5)
    filter_keywords(src, str(tmp_path / "f"), "Content", ["some"], 100)
    assert len(read_table(tmp_path / "s.xlsx")) == len(read_table(tmp_path / "f_unique.xlsx")) == 1


def test_filter_rejects_keyword_matching_a_column(tmp_path):
    pd.DataFrame({"Content": ["about Media"], "Media": [True]}).to_parquet(tmp_path / "in.parquet")
    with pytest.raises(SystemExit, match="Media"):
        filter_keywords(str(tmp_path / "in.parquet"), str(tmp_path / "f"), "Content", ["Media"], 10)


def test_resolve_inputs_rejects_folder_without_parquet(tmp_path):
    from telescraper.datafiles import resolve_inputs

    (tmp_path / "x.xlsx").write_bytes(b"")
    with pytest.raises(SystemExit, match="No .parquet files"):
        resolve_inputs(str(tmp_path))


@pytest.mark.parametrize("cmd, flag", [("filter", "--max-rows-per-file"), ("sample", "--sample-size")])
def test_cli_rejects_non_positive_sizes(cmd, flag):
    argv = [cmd, "--input", "x", "--output", "y", flag, "0"]
    if cmd == "filter":
        argv += ["--keywords", "k"]
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)
