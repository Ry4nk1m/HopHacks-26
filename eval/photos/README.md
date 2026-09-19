Put real, field-taken photos here, one folder per flag type and label:

```
eval/photos/
  tree_water/pass/   tree_water/fail/
  drain_clear/pass/  drain_clear/fail/
  cooling_check/pass/  cooling_check/fail/
  flood_report/present/  flood_report/absent/
  problem_report/present/  problem_report/absent/
```

`pass` / `present` = the task is really done or the problem is really visible. Include tricky negatives (a dry tree, a
half-cleared grate, a closed door with no hours). 10 to 20 per folder is plenty. Then run:

    .venv/bin/python -m eval.eval_validation --dir eval/photos

Photos are not committed (see .gitignore) since they may contain people or private property.
