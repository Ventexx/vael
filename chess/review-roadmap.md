# Game review: current model and next refinements

## Implemented

Review compares engine candidates at each main-line position and searches the preferred and played roots for equal time budgets. Initial searches use 0.5 seconds per call; a loss of at least four evaluation-index points, or a mate score, triggers a fresh candidate comparison and one-second root searches. This usually costs 1–5 seconds per move. Reviews run in a separate full-strength local engine process, stream progress, and can be cancelled. Only one review worker searches at a time.

The summary has White and Black accuracy estimates, move counts by grade, optional phase scores, and a chronological highlight tour. The tour includes costly moves for both sides and a strong decision from each side when available. Every move is also available from the graph, slider, and expandable move list.

Green arrows show the preferred move; dashed amber arrows show the played move. Both engine lines can be stepped through (up to eight plies). Previews are read-only: neither the variation tree nor the saved cursor changes. Closing Review restores the original board. Existing saved reviews must be analysed again to gain the new fields.

## Transparent, provisional scoring

These are Vael heuristics, not Chess.com's formula, a human win-probability forecast, or an Elo estimate.

For a centipawn score viewed from the mover's side, the evaluation index is:

`100 / (1 + exp(-centipawns / 250))`

Mate scores map to the corresponding extreme. Loss is the nonnegative difference between the best and played evaluation indices. Each non-forced move scores `100 * exp(-loss / 20)`; the player's accuracy is the arithmetic mean of these scores. Forced moves do not inflate the average. Short games are explicitly marked as short samples.

Grades:
- Forced: exactly one legal move.
- Best: the engine's preferred move.
- Great: a preferred move with a candidate-index gap of at least 15 and search depth at least 18, confirmed across searches. Captures, immediate mates, positions with at most two legal moves, and already decisive evaluations are excluded. This denotes a critical engine choice, not proof of human difficulty.
- Excellent: loss below 1.5 points and centipawn loss at most 60.
- Good: loss below 4 points.
- Inaccuracy: loss of at least 4 points.
- Mistake: loss of at least 10 points.
- Blunder: loss of at least 20 points.

The highest applicable loss category wins. Searches below depth 14 are marked provisional. A candidate search can miss a better move, and equivalent moves may exchange rankings between searches. If the constrained played search finds a higher score, it becomes the preferred candidate. These estimates need corpus calibration before stronger claims are justified.

Phases are deliberately simple: the first ten move numbers are opening unless already an endgame; an endgame begins at at most 26 total non-pawn material points (minor piece 3, rook 5, queen 9). Everything else is middlegame. Missing phases show no score.

## Explanations

Explanations combine the engine comparison with verifiable facts: checkmate, promotion, castling, captures, checks, attacks on major pieces, development, and material balance changes in the displayed continuation. They explicitly distinguish an engine continuation from what happened in the game. Quiet positions without a verified motif get an honest comparison rather than a fabricated strategic explanation.

## Remaining work

- Calibrate scoring against a diverse, human-reviewed corpus and measure stability at larger search budgets.
- Cache searches by full position history, engine version, and settings.
- Verify tactical motifs across the opponent's strongest replies, including sacrifices with delayed compensation, defensive resources, pins and discovered attacks.
- Add opening-book labels only with an actual opening database.
- Consider Brilliant only after sound sacrifice, compensation, and uniqueness can be verified. No Brilliant labels or estimated player ratings are currently generated.
- Expand explanations for quiet strategy and endgames only when backed by concrete evidence or tablebases.

## Verification and deeper checks

Individual moves can be rechecked with three- then six-second searches per candidate. Cancellation or failure keeps the previous row; completing a check refreshes the summary and highlights. Expandable evidence links show specific checks, captures and replies in legal engine continuations.

`tests/calibrate_review.py` records Stockfish results in `tests/review-calibration-report.json`: 44/44 synthetic position checks passed across both colors and two search budgets, with three Best/Excellent changes between budgets. Three short game controls also passed. These are regression controls, not independent human validation of the accuracy formula. Broader human-reviewed calibration remains necessary.
