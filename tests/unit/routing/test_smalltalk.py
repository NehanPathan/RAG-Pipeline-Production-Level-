"""Conversational turns, and the two ways they used to go wrong.

Asked *"may i know what u can do"* the assistant replied **"Hello."** — and
that was not a bug in one pattern, it was the meeting point of two.

The rules only recognised the bare phrasings, so a politely-worded capability
question fell through to the LLM classifier. The classifier got it *right*: its
prompt files "a greeting, thanks, or a question about your own capabilities"
under one `greeting` label, and that is what it returned. But only the rules
attach wording to a decision — the classifier carries none — so the graph used
its `"Hello."` default. Every classifier-routed conversational turn, whatever
it actually said, was answered with a bare greeting.

Two properties are pinned here, and the second matters more than the first:

* a conversational turn gets a reply that answers *it*, and
* a real question is never swallowed by any of it.

The second is the dangerous direction. These patterns are anchored to the whole
utterance precisely so that *"what can you tell me about the weld at CE-4"*
reaches retrieval instead of a capability blurb, and there are more tests below
for that than for the happy path.
"""

from __future__ import annotations

import pytest

from src.routing.router import _keep_document_questions
from src.routing.routes import Route, RouteDecision, RouteSource
from src.routing.rules import (
    GREETING_REPLY,
    IDENTITY_REPLY,
    OFF_TOPIC_REPLY,
    PLATFORM_REPLY,
    THANKS_REPLY,
    apply_rules,
    smalltalk_reply,
)


def _reply(query: str) -> str | None:
    """The wording a rule attaches, or None when no rule matched."""
    decision = apply_rules(query)
    return None if decision is None else decision.args.get("reply")


class TestTheReportedBug:
    def test_a_politely_worded_capability_question_is_not_answered_with_hello(self):
        """The exact query from the report, verbatim."""
        assert _reply("may i know what u can do") == IDENTITY_REPLY

    def test_and_it_no_longer_depends_on_the_classifier_supplying_wording(self):
        """Even routed by the classifier, which attaches none, the reply is
        derived from the query rather than defaulted."""
        assert smalltalk_reply("may i know what u can do") == IDENTITY_REPLY

    def test_a_bare_hello_still_greets(self):
        assert _reply("helooww") is None  # no rule; the classifier calls it a greeting
        assert smalltalk_reply("helooww") == GREETING_REPLY


class TestCapabilityQuestions:
    @pytest.mark.parametrize(
        "query",
        [
            "what can you do",
            "what all can you do",
            "what u can do",
            "what do u do",
            "pls tell me what u can do",
            "can you tell me what you can do",
            "may i know what u can do",
            "what can u help me with",
            "who are you",
            "what are u",
            "what is this",
            "what is this app",
            "how do you work",
            "how does it work",
            "what should i ask",
            "what kind of questions can i ask",
            "ur capabilities",
            "help",
            "help me",
        ],
    )
    def test_is_answered_with_what_the_system_actually_does(self, query):
        assert _reply(query) == IDENTITY_REPLY

    def test_the_answer_describes_this_corpus_not_a_generic_one(self):
        """It used to offer "policies, procedures, contracts and reports" —
        boilerplate from a generic RAG template, on a product whose whole
        subject is steel drawings. Someone asking what it does was told about
        a system that does not exist."""
        assert "drawing" in IDENTITY_REPLY.lower()
        assert "schedule" in IDENTITY_REPLY.lower()

    def test_the_answer_gives_a_question_they_could_actually_ask(self):
        """"Ask me about the documents" is not an answer to "what can you do"
        — it restates the problem. A worked example is."""
        assert "CE-4" in IDENTITY_REPLY or "BP-1" in IDENTITY_REPLY

    def test_the_answer_says_what_happens_when_it_does_not_know(self):
        """The grounding promise is part of the capability, not a caveat: it
        is the difference between this and a chatbot."""
        assert "cite" in IDENTITY_REPLY.lower()


class TestGreetingsAndThanks:
    @pytest.mark.parametrize(
        "query", ["hi", "hello", "hey", "good morning", "how are you", "what's up"]
    )
    def test_a_greeting_is_greeted(self, query):
        assert _reply(query) == GREETING_REPLY

    @pytest.mark.parametrize(
        "query", ["thanks", "thanks a lot", "thank u so much", "ty", "cheers", "ok", "bye"]
    )
    def test_an_acknowledgement_is_acknowledged(self, query):
        assert _reply(query) == THANKS_REPLY

    @pytest.mark.parametrize("query", ["helooww", "hellooo", "hii", "heyyy", "yooo", "helo"])
    def test_a_loosely_typed_greeting_is_still_a_greeting(self, query):
        """Matched literally these are nothing, and the off-topic reply would
        tell someone who said hello that their question was out of scope —
        worse than the bare "Hello." it replaced."""
        assert smalltalk_reply(query) == GREETING_REPLY

    def test_the_greeting_says_what_the_corpus_holds(self):
        """A greeting is the one moment someone is definitely reading, so it
        carries the orientation a bare "Hello." withheld."""
        assert "drawing" in GREETING_REPLY.lower()


class TestOffTopic:
    @pytest.mark.parametrize(
        "query",
        [
            "tell me a joke",
            "what is the capital of france",
            "sing me a song",
            "write me a poem about the weekend",
        ],
    )
    def test_is_redirected_rather_than_greeted(self, query):
        assert smalltalk_reply(query) == OFF_TOPIC_REPLY

    def test_the_redirect_names_the_topic_it_is_asking_for(self):
        """"Ask something on topic" without naming the topic just moves the
        guessing onto the person asking."""
        assert "drawing" in OFF_TOPIC_REPLY.lower()
        assert "CE-4" in OFF_TOPIC_REPLY or "base plate" in OFF_TOPIC_REPLY.lower()


class TestRealQuestionsAreNeverSwallowed:
    """The dangerous direction.

    Every pattern above is anchored to the whole utterance. Drop an anchor and
    these become capability blurbs and canned greetings instead of answers —
    silently, because a confident canned reply looks exactly like a real one.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "what can you tell me about the weld at CE-4",
            "what section is CE-4",
            "show me the schedule row for CE-4",
            "what do you do about corrosion on B-14",
            "how does the base plate connection work",
            "help me find the drawing for S-201",
            "what is this detail on sheet S-201",
            "what are you showing for the anchor bolts",
            "can you tell me the weight of CE-4",
            "what kind of questions does the spec answer about welds",
            "who are the approvers named in the title block",
            "what is this hatch pattern on S-104",
        ],
    )
    def test_reaches_the_retrieval_path(self, query):
        assert apply_rules(query) is None, "a conversational rule hijacked a real question"

    @pytest.mark.parametrize("query", ["hi, what section is CE-4", "hello what is BP-1 made of"])
    def test_a_greeting_stuck_on_the_front_does_not_make_it_smalltalk(self, query):
        assert apply_rules(query) is None


class TestAMisclassifiedQuestionStillReachesRetrieval:
    """The safety net under all of the above.

    A canned reply now says the system cannot help with the question. That is
    useful when true and a confident falsehood when the question was about a
    drawing — so a classifier guess of `greeting` on document vocabulary is
    overridden to retrieval, which ends in a grounded answer or an honest
    refusal rather than a polished brush-off.
    """

    def _classified(self, route: Route) -> RouteDecision:
        return RouteDecision(
            route=route, source=RouteSource.LLM, confidence=0.8, reason="fixed for the test"
        )

    @pytest.mark.parametrize(
        "query",
        [
            "what is the schedule row for CE-4",
            "which sheet shows the base plate detail",
            "what grade of steel is specified",
        ],
    )
    def test_a_document_question_called_a_greeting_is_sent_to_retrieval(self, query):
        result = _keep_document_questions(query, self._classified(Route.GREETING))
        assert result.route is Route.RAG

    def test_an_actual_greeting_is_left_alone(self):
        result = _keep_document_questions("hello", self._classified(Route.GREETING))
        assert result.route is Route.GREETING

    def test_a_rule_matched_greeting_is_never_second_guessed(self):
        """"Plan" and "section" are document words, and a rule that matched the
        whole utterance has already ruled that out. Only guesses are revisited.
        """
        rule = RouteDecision(
            route=Route.GREETING,
            source=RouteSource.RULE,
            confidence=1.0,
            reason="matched greeting pattern",
        )
        assert _keep_document_questions("hi", rule).route is Route.GREETING

    def test_the_override_only_ever_moves_toward_retrieval(self):
        """The invariant the whole guard rests on: it can add a search, never
        remove one, so it cannot weaken grounding."""
        for route in (Route.RAG, Route.CALCULATOR, Route.DATETIME):
            decision = self._classified(route)
            assert _keep_document_questions("what section is CE-4", decision).route is route


class TestPlatformQuestions:
    """Questions about the workspace rather than about the drawings.

    *"may get user imformation who loggin"* spent eleven seconds embedding,
    searching two stores, fusing, reranking and compressing before refusing.
    The refusal was correct — the user directory is not in the corpus — and
    useless, because the answer exists and is one screen away.

    Answered from a rule now: no retrieval, no model call, and a pointer to
    where the question is actually answered.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "may get user imformation who loggin",
            "may i get user information who logged in",
            "who is logged in",
            "who is currently signed in",
            "show me all users",
            "list users",
            "user information",
            "how many users",
            "what is my role",
            "my permissions",
            "reset my password",
            "who has access to this system",
        ],
    )
    def test_is_answered_without_touching_retrieval(self, query):
        assert _reply(query) == PLATFORM_REPLY

    def test_the_answer_names_the_screen_rather_than_only_declining(self):
        """"Not in the documents" is true and unhelpful on its own. The person
        asking wants the roster, not a boundary."""
        assert "Access" in PLATFORM_REPLY

    def test_the_answer_offers_the_drawing_reading_of_the_question(self):
        """"Who approved this" is a real question about a title block, and
        someone who meant that should not be left thinking it cannot be
        asked."""
        assert "title" in PLATFORM_REPLY.lower()


class TestThePlatformVetoProtectsDrawingQuestions:
    """The rule matches the word *user*, and a drawing set is full of
    legitimate questions that use it. The veto is what makes the rule safe:
    anything naming a document goes to retrieval regardless.

    A false negative costs one retrieval that ends in the refusal it would
    have reached anyway. A false positive answers a real drawing question with
    a pointer to a settings screen — so these matter more than the tests above.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "who approved drawing S-201",
            "which user signed off the inspection on S-104",
            "who is the engineer named in the title block",
            "who signed the drawing",
            "what users are listed on the revision schedule",
            "who checked sheet S-101",
            "who is logged in the site diary for S-201",
            "list the members on the schedule",
            "how many members are in the BOM",
        ],
    )
    def test_reaches_retrieval(self, query):
        assert apply_rules(query) is None, "the platform rule swallowed a drawing question"
