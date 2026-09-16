# Getting started

Machine-learning work goes through this power. Start with the `ml-planning` skill and
follow the workflow it defines; it decides which stages run and in what order.

Two things must happen before any code is generated, and `ml-planning` covers both:
resolve the environment, and write the plan down. Everything else is a stage.

## Keeping this active in a project

Steering that lives in the power applies while the power is engaged. To make it apply
unconditionally in a project — which is what removes the guesswork about whether the
skill loads — copy this file into the project:

```bash
mkdir -p .kiro/steering
cp ~/.kiro/powers/installed/sagemaker-ml-workflow/steering/getting-started.md \
   .kiro/steering/ml-workflow.md
```

Measured on one machine with one model: a plain request activated `ml-planning` twice
out of four times without a project steering file, and three out of three with one.
Project steering is loaded unconditionally rather than matched, which is the whole
difference.
