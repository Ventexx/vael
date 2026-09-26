# Deeper game review — proposed next stage

The current review is a quick local Stockfish pass: evaluation graph, biggest evaluation drops, position navigation, and the engine's suggested alternative. It deliberately does not assign move grades yet.

## Reliable move grades

Analyse each position with several candidate moves, then analyse the played move with a comparable search budget. Use winning-chance loss alongside evaluation loss, the game phase, and whether the move was forced. Recheck large swings with a deeper search before displaying a confident verdict. Cache results by position and engine settings.

Start with best, excellent, good, inaccuracy, mistake, and blunder. Calibrate boundaries against a reviewed position set, including forced mates, promotions, draws, and already-lost positions. Treat opening-book moves separately. These would be Vael's own labels, not a reproduction of Chess.com's proprietary grading.

Reserve “brilliant” for verified exceptional moves: a meaningful sacrifice, sound compensation after the strongest reply, and a clear reason the move is difficult or uniquely effective. Material loss alone is insufficient. When the evidence is weak, use a normal grade or say the position needs deeper analysis.

## Short explanations grounded in the position

Compare the played move with the strongest alternatives and their principal variations. Detect concrete evidence such as a hanging piece, fork, pin, mating threat, passed pawn, or forced material gain. Explain the consequence in one or two sentences and let the user expand the supporting line. For quiet positional moves, acknowledge uncertainty rather than inventing a tactical reason.

Begin with local templates backed by board rules and verified engine lines. Optional generated prose could come later, but only from these structured facts and with checks that every cited move is legal. Never infer an explanation solely from the evaluation number.

## Keep the interface quiet

Use the existing Review button and drawer. Add a small grade beside the selected move, a single explanation, and an expandable “Compare alternatives” section. Show a summary only when review is opened; avoid another permanent panel or badges on every square. Allow deeper analysis for an individual move without rerunning the entire game.

## Validation before release

Use a curated corpus with human-reviewed tactical and positional examples. Test colour symmetry, equivalent transpositions, forced moves, mate-distance changes, insufficient material, underpromotions, and engine-budget stability. A grade should not fluctuate dramatically after a small increase in depth; uncertain results should remain provisional.
