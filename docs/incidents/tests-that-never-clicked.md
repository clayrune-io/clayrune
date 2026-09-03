# The tests covered the endpoint, so nobody clicked the button

We have a 1,797-test suite. A rename broke a tab so that every click threw a
`ReferenceError` and no viewer opened, and the suite stayed green through all of
it.

## What broke

The rename was Plans to Documents. One identifier got left behind: the viewer's
filename line still referred to a variable that no longer existed in the renamed
handler. Every click on a row threw.

It read like a backend problem and was not one. The endpoint returned 200 with
the full document content. The fetch inside the handler had already resolved
before the throw. The row simply did nothing, quietly, with a clean network tab.

## Why the suite did not care

Look at what was actually covered:

- A test that calls the endpoint passes. The endpoint was fine.
- A test that asserts the handler exists passes. The handler existed.
- The user-visible behaviour, which is that clicking a row opens a viewer, was
  covered by neither.

Nothing in the suite ever performed the sequence a person performs. Coverage was
high and it was measured on a surface nobody touches.

## The general shape

This is the pattern we now expect from tests written alongside the code they
test, and especially from tests written by an agent: **they cover the boundary
that is visible in the file being edited.** A handler, a route, a return value,
an exported function. Those are the seams the author can see while writing.

The two-second sequence a person performs is not written down in any file, so
nothing prompts anyone to test it. A rename that keeps every boundary intact and
breaks only the wiring between them passes cleanly.

Coverage percentage will not warn you about this, because the uncovered thing is
not a line of code. It is a path between lines that all have tests.

## What worked

One browser test that drives the real running application through the real
interface: the rows render, a click opens the viewer, the viewer holds real
content, and no uncaught error escapes the click.

Then the important half. We stashed the fix and ran it again to confirm it
failed, and it failed on three separate checks.

**A test you have not watched fail is a test you are guessing about.** Writing
the test after the fix is the normal case and it is fine, but it only proves
anything if you put the bug back and watch the test catch it. Otherwise you have
added a test that passes, which is not the same as adding a test that works.
