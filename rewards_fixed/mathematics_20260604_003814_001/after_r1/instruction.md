The file `/app/challenge.py` contains a sequence of 20 integers produced by an unknown linear congruential generator (LCG). An LCG computes:

```
X_{n+1} = (a * X_n + c) mod m
```

with unknown parameters `a`, `c`, and `m`. Your task is to recover these parameters from the observed sequence and then predict the next 10 values that the generator would produce.

Write your solution to `/app/solve.py` with a function `solve()` that returns a list of 10 integers: the next 10 values the LCG would output after the last observed value.
