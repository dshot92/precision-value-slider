# Precision Value Slider

Hover the mouse over a number field and hold "Middle Mouse". A vertical precision ladder appears at the cursor. The slider only adds or subtracts the selected step.

The ladder always shows every step, whatever the value. A 32 bit float holding 5.6 million is already 0.5 apart, so a `.0001` step has nowhere to land in one tick; the slide keeps the exact running total and the field takes the nearest value it can hold, so the value moves once enough ticks accumulate instead of jumping by a wrong amount. A Python custom property (float64) keeps those digits directly.

Move vertically to pick the step; horizontal motion slides the value and keeps the step fixed. Move vertically again at any point, even after a long slide, to take control of the ladder and change the step. The ladder only responds while the cursor is moving slowly: a fast drag just slides the value. A one-step deadzone keeps small jitter from switching axes.

The ladder is drawn above all UI: it stays fully visible even in an editor too short to hold it, spilling over the neighbouring editors instead of being clipped.

## Preferences

- **Ladder Steps Above / Below**: the hard precision limit and the number of coarser/finer steps shown around the unit step (`10⁰`, default 4).
- **Pixels per Step**: horizontal drag distance, in UI pixels, for one step (default 10).
