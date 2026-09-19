# Walk test checklist

Before you leave
- [ ] Server running with `DEV_TOOLS=1`, Gemini key set, tunnel URL open on your phone (HTTPS).
- [ ] Sign up, allow **location** and **camera** when the browser asks. Add the page to your home screen for full screen.
- [ ] Weather is mild today, so force a trigger in Profile > Demo tools (for example *Dry spell: On*) so tree flags exist.

On the route (around Homewood / Charles Village)
- [ ] The blue dot follows you; accuracy shows in the report screen. Note how far it drifts between buildings.
- [ ] A cooling-space flag: stand at the entrance, take a photo of the hours sign. Did Gemini read the hours?
- [ ] A tree flag: water it, photograph the wet soil at the base. Try one **before** photo too (+5 points).
- [ ] A 311 flag (dirty street, fallen tree): does the photo check tell "still there" from "cleaned up"?
- [ ] File one report yourself (Report button). It should appear as a flag; a second phone can confirm it.
- [ ] Deliberately test a bad photo: too dark, too far, a photo of your shoe. It should be rejected with a clear message.
- [ ] Walk away from a flag and tap **I'm here**: it should refuse beyond about 150 m and accept nearby.

Write down
- Photos that were wrongly rejected or wrongly accepted (put them in `eval/photos/` with the right label).
- Moments GPS made a flag feel misplaced.
- Any screen that confused you.

Afterwards run `python -m eval.eval_validation --dir eval/photos` and `python -m eval.eval_satellite`.
