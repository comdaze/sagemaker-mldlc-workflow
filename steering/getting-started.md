# Getting started

Machine-learning work goes through this power. Start with the `ml-planning` skill and
follow the workflow it defines; it decides which stages run and in what order.

Two things must happen before any code is generated, and `ml-planning` covers both:
resolve the environment, and write the plan down. Everything else is a stage.

## Keeping this active in a project

You are reading this because the file was copied into the project, which is one of two
ways to reach this workflow reliably. The other needs no file: typing `/ml-planning` in
the composer calls the skill by name, and importing the power registers all five as
slash commands.

The file exists to remove even that. Steering is loaded unconditionally rather than
matched, so nothing has to be typed and nothing has to be remembered. If it is not yet
in the project:

```bash
mkdir -p .kiro/steering
cp ~/.kiro/powers/installed/sagemaker-mldlc-workflow/steering/getting-started.md \
   .kiro/steering/ml-workflow.md
```

Measured on one machine with one model: a plain request engaged `ml-planning` in two of
four attempts without that file, and three of three with it. Calling it by name skips
the matching that produces those odds.
