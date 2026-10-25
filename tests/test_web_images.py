"""Picture search: only https pictures, the search engine's own small copy first, no duplicates, a sane count."""

from __future__ import annotations

from loamz.solutions.web.images import ImageFinder

HITS = [
    {
        "title": "  Spain   lift the trophy ",
        "thumbnail": "https://t.example/1.jpg",
        "image": "https://big.example/1.jpg",
        "url": "https://news.example/a",
        "width": 474,
        "height": 266,
    },
    {
        "title": "dup",
        "thumbnail": "https://t.example/1.jpg",
        "image": "https://big.example/dup.jpg",
        "url": "https://news.example/b",
    },
    {
        "title": "only the original",
        "image": "https://big.example/2.jpg",
        "url": "http://news.example/c",
    },
    {
        "title": "plain http",
        "thumbnail": "http://t.example/3.jpg",
        "image": "http://big.example/3.jpg",
    },
    {"title": "a login in the address", "thumbnail": "https://user:pw@t.example/4.jpg"},
    {"title": "javascript", "thumbnail": "javascript:alert(1)"},
    {"title": "no address"},
]


def finder(hits=HITS) -> ImageFinder:
    return ImageFinder(lambda query, limit: hits)


def test_it_returns_https_pictures_preferring_the_thumbnail_without_duplicates():
    pictures = finder().find("spain", limit=10)
    assert [p.address for p in pictures] == ["https://t.example/1.jpg", "https://big.example/2.jpg"]
    assert (
        pictures[0].title == "Spain lift the trophy"
        and pictures[0].page == "https://news.example/a"
        and (pictures[0].width, pictures[0].height) == (474, 266)
    )
    assert pictures[1].page == ""  # its page was plain http: not offered


def test_the_count_is_kept_sane():
    many = [{"title": str(i), "thumbnail": f"https://t.example/{i}.jpg"} for i in range(30)]
    assert len(finder(many).find("x", limit=3)) == 3
    assert len(finder(many).find("x", limit=999)) == 10
    assert len(finder(many).find("x", limit=0)) == 1


def test_the_text_for_the_model_names_each_picture_address_and_page():
    text = finder().describe("spain", 2)
    assert (
        "picture: https://t.example/1.jpg" in text
        and "page: https://news.example/a" in text
        and "(474x266)" in text
    )
    assert finder([]).describe("nothing") == "No pictures found for: nothing"
