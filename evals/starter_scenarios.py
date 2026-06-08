"""Starter scenarios for the Scout triage Test Suite.

Each item is a TestSuiteItem dict ready to hand to `TestSuite.insert(...)`. The
`data` field is what evals/run_eval.py's task() consumes — `scenario` picks the
builder, `spec` populates the GitHubSimulator, `target_issue` is the issue
Scout triages this run.

To add a scenario, append to STARTER_SCENARIOS and re-run seed_test_suite.py.
"""
from __future__ import annotations


# Shared README used by the trainer-lib scenarios.
TRAINER_LIB_README = """\
# trainer-lib

A lightweight PyTorch trainer for distributed training across multiple GPUs.
Supports auto-tuning of batch size and learning rate, YAML-based configs,
and a CLI entry point: `python -m trainer fit --config path/to/config.yaml`.
"""


# ---------------------------------------------------------------------------
# Scenario 1: clear duplicate exists and is findable by an obvious search
# ---------------------------------------------------------------------------

_SIMPLE_DUPLICATE = {
    "description": "simple-duplicate-cite-issue",
    "data": {
        "scenario_id": "simple-duplicate-cite-issue",
        "scenario": "default",
        "spec": {
            "owner": "trainer-org",
            "name": "trainer-lib",
            "readme": TRAINER_LIB_README,
            "issues": [
                {
                    "number": 999,
                    "title": "Training hangs when I use 4 GPUs",
                    "body": (
                        "When I launch `trainer.fit()` with `gpus=4`, the process "
                        "deadlocks during the first epoch. Single-GPU works fine. "
                        "No error message, just hangs forever."
                    ),
                    "author": "alice",
                    "state": "open",
                    "labels": [],
                },
                {
                    "number": 412,
                    "title": "multi-gpu deadlock in fit()",
                    "body": (
                        "Same deadlock seen here. Root cause was in "
                        "src/distributed/all_reduce.py — the process group wasn't "
                        "being initialized before the first reduce. Workaround: "
                        "pass `init_method='env://'` when constructing the group."
                    ),
                    "author": "bob",
                    "state": "closed",
                    "labels": ["bug", "distributed"],
                },
            ],
            "files": {
                "src/trainer.py": (
                    "from .distributed.all_reduce import all_reduce\n"
                    "\n"
                    "class Trainer:\n"
                    "    def fit(self, gpus: int = 1):\n"
                    "        if gpus > 1:\n"
                    "            all_reduce(self.gradients)\n"
                    "        # ... training loop ...\n"
                ),
                "src/distributed/all_reduce.py": (
                    "import torch.distributed as dist\n"
                    "\n"
                    "def all_reduce(tensors):\n"
                    "    # BUG: process group is never initialized before the\n"
                    "    # first reduce on multi-GPU runs.\n"
                    "    for t in tensors:\n"
                    "        dist.all_reduce(t)\n"
                ),
                "tests/test_distributed.py": (
                    "def test_single_gpu_path():\n"
                    "    # no multi-GPU coverage\n"
                    "    pass\n"
                ),
            },
        },
        "target_issue": 999,
        "expected": {
            "should_escalate": False,
            "should_cite_issue": 412,
            "root_cause_files": ["src/distributed/all_reduce.py"],
        },
    },
    "assertions": [
        "The response references issue #412 as a related or duplicate issue.",
        "The response identifies src/distributed/all_reduce.py as where the bug lives.",
        "final_labels does not contain 'Escalated request'.",
        "The response includes a Solution / Workaround section with the env:// workaround from #412.",
        "search_queries contains at least one query that mentions 'gpu', 'multi', 'hang', or 'deadlock'.",
        "The response notes the gap in test coverage for the multi-GPU path.",
    ],
}


# ---------------------------------------------------------------------------
# Scenario 2: real bug with a clear source location, no prior duplicate
# ---------------------------------------------------------------------------

_CLEAR_BUG_NO_DUPLICATE = {
    "description": "clear-bug-no-duplicate",
    "data": {
        "scenario_id": "clear-bug-no-duplicate",
        "scenario": "default",
        "spec": {
            "owner": "trainer-org",
            "name": "trainer-lib",
            "readme": TRAINER_LIB_README,
            "issues": [
                {
                    "number": 777,
                    "title": "CLI flag --batch-size is silently ignored when --auto-tune=true",
                    "body": (
                        "Running `python -m trainer fit --batch-size=32 --auto-tune=true` "
                        "completely ignores --batch-size. The auto-tune logic overwrites "
                        "it. Expected: --batch-size should be respected as an upper bound, "
                        "or the CLI should error out instead of silently dropping the flag."
                    ),
                    "author": "carol",
                    "state": "open",
                    "labels": [],
                },
            ],
            "files": {
                "src/cli.py": (
                    "import argparse\n"
                    "from .auto_tune import auto_tune_batch_size\n"
                    "\n"
                    "def main():\n"
                    "    parser = argparse.ArgumentParser()\n"
                    "    parser.add_argument('--batch-size', type=int, default=32)\n"
                    "    parser.add_argument('--auto-tune', type=bool, default=False)\n"
                    "    args = parser.parse_args()\n"
                    "\n"
                    "    if args.auto_tune:\n"
                    "        # BUG: --batch-size is dropped here. Should be passed as\n"
                    "        # an upper bound to auto_tune_batch_size.\n"
                    "        args.batch_size = auto_tune_batch_size()\n"
                    "    run_trainer(batch_size=args.batch_size)\n"
                ),
                "src/auto_tune.py": (
                    "def auto_tune_batch_size(upper_bound: int | None = None) -> int:\n"
                    "    # Walks batch sizes; respects upper_bound if provided.\n"
                    "    sizes = [16, 32, 64, 128, 256]\n"
                    "    if upper_bound is not None:\n"
                    "        sizes = [s for s in sizes if s <= upper_bound]\n"
                    "    return max(sizes)\n"
                ),
                "tests/test_cli.py": (
                    "def test_batch_size_flag_alone():\n"
                    "    # no test for --batch-size + --auto-tune combination\n"
                    "    pass\n"
                ),
            },
        },
        "target_issue": 777,
        "expected": {
            "should_escalate": False,
            "should_cite_issue": None,
            "root_cause_files": ["src/cli.py"],
        },
    },
    "assertions": [
        "The response identifies src/cli.py as the file containing the bug.",
        "The response describes a concrete fix — passing --batch-size as an upper bound to auto_tune_batch_size.",
        "final_labels does not contain 'Escalated request'.",
        "The response includes a Solution / Workaround section.",
        "The response invites the reporter to open a PR with the fix.",
    ],
}


# ---------------------------------------------------------------------------
# Scenario 3: large breaking change — escalation expected
# ---------------------------------------------------------------------------

_ESCALATION_BREAKING_CHANGE = {
    "description": "escalation-breaking-change",
    "data": {
        "scenario_id": "escalation-breaking-change",
        "scenario": "default",
        "spec": {
            "owner": "trainer-org",
            "name": "trainer-lib",
            "readme": TRAINER_LIB_README,
            "issues": [
                {
                    "number": 555,
                    "title": "Replace YAML configs with TOML across the board",
                    "body": (
                        "YAML is error-prone (indentation, type coercion surprises) and "
                        "slow to parse for large hyperparam sweeps. I propose we migrate "
                        "all trainer configs, sweep configs, and example configs from "
                        "YAML to TOML. Every existing user config would need to be "
                        "rewritten, but TOML is a better long-term foundation."
                    ),
                    "author": "dan",
                    "state": "open",
                    "labels": [],
                },
            ],
            "files": {
                "src/config/loader.py": (
                    "import yaml\n"
                    "\n"
                    "def load_config(path: str) -> dict:\n"
                    "    with open(path) as f:\n"
                    "        return yaml.safe_load(f)\n"
                ),
                "examples/config.yaml": (
                    "trainer:\n"
                    "  batch_size: 32\n"
                    "  lr: 0.001\n"
                ),
                "docs/configuration.md": (
                    "# Configuration\n\n"
                    "trainer-lib uses YAML for all configuration. See examples/ for templates.\n"
                ),
                "src/sweep/runner.py": (
                    "from ..config.loader import load_config\n"
                    "\n"
                    "def run_sweep(sweep_yaml: str):\n"
                    "    cfg = load_config(sweep_yaml)\n"
                    "    # ...\n"
                ),
            },
        },
        "target_issue": 555,
        "expected": {
            "should_escalate": True,
            "should_cite_issue": None,
            "root_cause_files": ["src/config/loader.py"],
        },
    },
    "assertions": [
        "final_labels contains 'Escalated request'.",
        "applied_labels contains 'Escalated request'.",
        "The response explains that this is a breaking change affecting every existing user config.",
        "The response identifies src/config/loader.py and mentions the broader surface (sweep configs, docs, examples).",
        "The response's Next Steps section mentions the need for a design decision or team review.",
    ],
}


# ---------------------------------------------------------------------------
# Scenario 4: spam / off-topic — short response, no full triage
# ---------------------------------------------------------------------------

_SPAM_OFF_TOPIC = {
    "description": "spam-off-topic",
    "data": {
        "scenario_id": "spam-off-topic",
        "scenario": "default",
        "spec": {
            "owner": "trainer-org",
            "name": "trainer-lib",
            "readme": TRAINER_LIB_README,
            "issues": [
                {
                    "number": 321,
                    "title": "Boost your website traffic with our SEO experts!",
                    "body": (
                        "Hello team! We are a leading SEO agency offering professional "
                        "services to help your project rank #1 on Google. Our clients "
                        "have seen 500% traffic growth. Contact us today for a free "
                        "consultation and a custom growth plan!"
                    ),
                    "author": "seo-spammer-99",
                    "state": "open",
                    "labels": [],
                },
            ],
            "files": {
                "src/trainer.py": "class Trainer: ...\n",
            },
        },
        "target_issue": 321,
        "expected": {
            "should_escalate": False,
            "should_cite_issue": None,
            "root_cause_files": [],
            "is_spam": True,
        },
    },
    "assertions": [
        "The response notes the issue is unrelated to the trainer-lib project.",
        "The response does NOT contain a 'Code Investigation' section.",
        "The response does NOT contain a 'Solution / Workaround' section.",
        "final_labels does not contain 'Escalated request'.",
        "The response is brief — five sentences or fewer.",
    ],
}


# ---------------------------------------------------------------------------
# Scenario 5: search degrades mid-run — output should still be well-formed
# ---------------------------------------------------------------------------

_SEARCH_RATE_LIMITED = {
    "description": "search-rate-limited-resilience",
    "data": {
        "scenario_id": "search-rate-limited-resilience",
        "scenario": "search-rate-limited",
        # Same spec as scenario 1, but the builder swaps in a search handler
        # that returns [] after the second call.
        "spec": _SIMPLE_DUPLICATE["data"]["spec"],
        "target_issue": 999,
        "expected": {
            "should_escalate": False,
            "should_cite_issue": None,  # we don't require citing #412 here —
                                        # search may fail before Scout finds it
            "root_cause_files": ["src/distributed/all_reduce.py"],
        },
    },
    "assertions": [
        "The response begins with the Scout greeting line ('Hi, I'm Scout 🦉').",
        "The response includes all three required sections: Solution / Workaround, Code Investigation, Next Steps.",
        "The response identifies src/distributed/all_reduce.py as the relevant file (via list_directory / get_file_contents, not via search).",
        "final_labels does not contain 'Escalated request'.",
    ],
}


# ---------------------------------------------------------------------------
# Scenario 6: multi-party comment thread — respond to the latest comment,
# weighing a maintainer's input, building on Scout's own prior reply
# ---------------------------------------------------------------------------

_COMMENT_THREAD = {
    "description": "comment-thread-maintainer-followup",
    "data": {
        "scenario_id": "comment-thread-maintainer-followup",
        "scenario": "default",
        "spec": {
            "owner": "trainer-org",
            "name": "trainer-lib",
            "readme": TRAINER_LIB_README,
            "issues": [
                {
                    "number": 888,
                    "title": "Resuming from a checkpoint loses optimizer state",
                    "body": (
                        "When I resume training with `--resume path/to/ckpt.pt`, the "
                        "model weights come back but the optimizer state does not — "
                        "loss spikes for a few hundred steps as Adam's moment "
                        "estimates re-warm up. Expected: resuming restores optimizer "
                        "state too, so training continues seamlessly."
                    ),
                    "author": "alice",
                    "author_association": "NONE",
                    "state": "open",
                    "labels": [],
                    "comments": [
                        {
                            "author": "carol",
                            "association": "CONTRIBUTOR",
                            "body": (
                                "Confirmed on my end. Looks like `load_checkpoint` in "
                                "src/checkpoint.py only restores `model.state_dict()` "
                                "and never touches the optimizer."
                            ),
                        },
                        {
                            # A prior Scout reply — rendered as an assistant turn.
                            "author": "scout-bot",
                            "association": "NONE",
                            "body": (
                                "Hi, I'm Scout 🦉. Early read: the gap looks like it's "
                                "in `src/checkpoint.py` — `load_checkpoint` restores "
                                "model weights but not optimizer state. Digging further."
                            ),
                        },
                        {
                            "author": "bob",
                            "association": "MEMBER",
                            "body": (
                                "Agreed this is the spot. @scout before we fix it: is "
                                "src/checkpoint.py the only place we'd change, or does "
                                "Trainer need to construct the optimizer *before* "
                                "loading so there's a state dict to load into?"
                            ),
                        },
                    ],
                },
            ],
            "files": {
                "src/checkpoint.py": (
                    "import torch\n"
                    "\n"
                    "def save_checkpoint(path, model, optimizer):\n"
                    "    torch.save({\n"
                    "        'model': model.state_dict(),\n"
                    "        'optimizer': optimizer.state_dict(),\n"
                    "    }, path)\n"
                    "\n"
                    "def load_checkpoint(path, model):\n"
                    "    # BUG: only the model is restored. The saved 'optimizer'\n"
                    "    # state dict is ignored, so Adam moments reset on resume.\n"
                    "    ckpt = torch.load(path)\n"
                    "    model.load_state_dict(ckpt['model'])\n"
                ),
                "src/trainer.py": (
                    "from .checkpoint import load_checkpoint\n"
                    "\n"
                    "class Trainer:\n"
                    "    def fit(self, resume: str | None = None):\n"
                    "        self.model = build_model()\n"
                    "        if resume:\n"
                    "            # Optimizer is created AFTER load — there's nothing\n"
                    "            # to load optimizer state into at this point.\n"
                    "            load_checkpoint(resume, self.model)\n"
                    "        self.optimizer = build_optimizer(self.model)\n"
                    "        # ... training loop ...\n"
                ),
                "tests/test_checkpoint.py": (
                    "def test_save_load_roundtrip_model():\n"
                    "    # only asserts model weights match; optimizer not covered\n"
                    "    pass\n"
                ),
            },
        },
        "target_issue": 888,
        "expected": {
            "should_escalate": False,
            "should_cite_issue": None,
            "root_cause_files": ["src/checkpoint.py", "src/trainer.py"],
        },
    },
    "assertions": [
        "The response directly answers bob's question about whether src/checkpoint.py is the only change needed.",
        "The response notes that Trainer constructs the optimizer after load_checkpoint, so the optimizer must be created before loading its state (src/trainer.py).",
        "The response identifies src/checkpoint.py — load_checkpoint ignores the saved optimizer state dict.",
        "The response reflects the thread: it acknowledges the maintainer/contributor confirmation rather than re-deriving the cause from scratch.",
        "final_labels does not contain 'Escalated request'.",
    ],
}


STARTER_SCENARIOS = [
    _SIMPLE_DUPLICATE,
    _CLEAR_BUG_NO_DUPLICATE,
    _ESCALATION_BREAKING_CHANGE,
    _SPAM_OFF_TOPIC,
    _SEARCH_RATE_LIMITED,
    _COMMENT_THREAD,
]


# Global assertions checked against every item (added at suite-creation time).
GLOBAL_ASSERTIONS = [
    "The response is technical and not condescending.",
    "The response does not include placeholder text like '[TODO]' or unfilled template variables like $repo_owner.",
]


GLOBAL_EXECUTION_POLICY = {
    # Run each scenario once by default. Bump runs_per_item to 2-3 to measure
    # variance from sampling temperature; raise pass_threshold accordingly.
    "runs_per_item": 1,
    "pass_threshold": 1,
}
