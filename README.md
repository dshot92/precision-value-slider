# Precision Value Slider

Hover the mouse over a number field and hold "Middle Mouse". A vertical precision ladder appears at the cursor. The slider only adds or subtracts the selected step.

# Limitations
Currently blender code does not allow this to work on popup panel and some other inputs that are not exposed in python.

## Preferences

- **Ladder Steps Above / Below**: the hard precision limit and the number of coarser/finer steps shown around the unit step (`10⁰`, default 3).
- **Pixels per Step**: horizontal drag distance, in UI pixels, for one step (default 10).
