"""Tests of split_sentences(), used to cut paragraphs longer than 500 words."""

import pytest

from app.pipeline.sentences import split_sentences


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("One sentence.", ["One sentence."], id="one sentence"),
        pytest.param("", [""], id="empty"),
        pytest.param("First. Second! Third? Fourth…", ["First.", "Second!", "Third?", "Fourth…"]),
        pytest.param("Wait… Then go.", ["Wait…", "Then go."], id="ellipsis character"),
        pytest.param("Wait... Then go.", ["Wait...", "Then go."], id="three dots"),
        pytest.param("Wait... and go.", ["Wait... and go."], id="three dots, lower case"),
        pytest.param(
            "“I am.” “You are.”", ["“I am.”", "“You are.”"], id="closing and opening quotes"
        ),
        pytest.param('He left.) Then. "Yes."', ["He left.)", "Then.", '"Yes."'], id="bracket"),
        pytest.param("It ended. — Not yet.", ["It ended.", "— Not yet."], id="dash"),
        pytest.param("Ovo je kraj. Čovjek ode.", ["Ovo je kraj.", "Čovjek ode."], id="Č"),
        pytest.param("3.5 is a number. so is 4.", ["3.5 is a number. so is 4."], id="lower case"),
    ],
)
def test_boundaries(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "She saw Mr. Darcy at the ball.",
        "Then Mrs. Bennet spoke.",
        "A play by J. Comyns Carr was staged.",
        "Books by J.R.R. Tolkien sell well.",
        "Gradovi, npr. Podgorica i Nikšić, rastu.",
        "To je, tj. Marko, došao.",
        "Apples, pears, etc. and more.",
        "Apples, pears, etc. Then more.",
        "Ask Dr. Watson and dr. Petrović.",
        "Došla je gđa. Jovanović.",
        "The quarrel of Smith vs. Jones went on.",
        "Louis XIV. “the sun king” ruled.",
        "Luj XIV. – kralj sunca.",
        "“Mr. Bingley is here.”",
    ],
)
def test_no_boundary_after_abbreviations_initials_and_roman_numerals(text: str) -> None:
    assert split_sentences(text) == [text]


def test_npr_before_lower_case_is_one_sentence() -> None:
    assert split_sentences("Voće, npr. ovo jabuke.") == ["Voće, npr. ovo jabuke."]


def test_roman_numeral_before_capital_is_a_boundary() -> None:
    assert split_sentences("He was Louis XIV. The king died.") == [
        "He was Louis XIV.",
        "The king died.",
    ]


def test_abbreviation_inside_a_closing_quote_is_a_boundary() -> None:
    assert split_sentences("He said “Mr.” Then he left.") == ["He said “Mr.”", "Then he left."]


@pytest.mark.parametrize(
    "text",
    [
        "First. Second! Third? Fourth… Fifth... Sixth.",
        "She saw Mr. Darcy. Mrs. Bennet said, “Oh!” Then J. Comyns Carr came.",
        "Two  spaces. Here",
        " Leading space. And trailing. ",
        "No full stop at all",
        "Npr. Marko i dr. Janković, itd. Kraj.",
    ],
)
def test_joining_the_sentences_gives_the_text_back(text: str) -> None:
    assert " ".join(split_sentences(text)) == text
