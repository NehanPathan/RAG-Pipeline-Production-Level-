"""Online (production-traffic) evaluation — the MEASURE function's live half.

Offline evaluation answers "was quality acceptable on 50 curated questions
the last time someone ran it". That is necessary and not sufficient: it says
nothing about what real users are asking today, and nothing about drift
between runs. This package closes that gap two ways:

    sampler.py   passive — score a deterministic sample of live answers with
                 the same judge the offline runner uses, so the two numbers
                 are directly comparable rather than two different scales.
    feedback.py  active — capture explicit user ratings, and promote negative
                 ones into the golden dataset so the next offline run covers
                 the failure a user actually hit (the MANAGE feedback loop).
"""
