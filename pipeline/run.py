# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

import os
from os import path as op
import sys
import pkg_resources
import logging

os.environ["NIPYPE_NO_ET"] = "1"  # disable nipype update check
os.environ["NIPYPE_NO_MATLAB"] = "1"

if op.isdir("/home/fmriprep/.cache/pipeline"):
    os.environ["PIPELINE_RESOURCE_DIR"] = "/home/fmriprep/.cache/pipeline"
    os.environ["TEMPLATEFLOW_HOME"] = "/home/fmriprep/.cache/templateflow"

from fmriprep import config  # noqa

configfilename = pkg_resources.resource_filename("pipeline", "data/config.toml")
config.load(configfilename)

global debug
debug = False


def _main():
    from . import __version__
    from argparse import ArgumentParser
    from multiprocessing import cpu_count

    ap = ArgumentParser(
        description=f"mindandbrain/pipeline {__version__} is a user-friendly interface "
        "for performing reproducible analysis of fMRI data, including preprocessing, "
        "single-subject feature extraction, and group analysis."
    )

    basegroup = ap.add_argument_group("base", "")

    basegroup.add_argument(
        "--workdir", type=str, help="directory where output and intermediate files are stored",
    )
    basegroup.add_argument("--fs-root", default="/ext", help="path to the file system root")
    basegroup.add_argument("--debug", action="store_true", default=False)
    basegroup.add_argument("--verbose", action="store_true", default=False)
    basegroup.add_argument("--watchdog", action="store_true", default=False)

    stepgroup = ap.add_argument_group("steps", "")
    steps = ["spec-ui", "workflow", "execgraph", "run", "run-subjectlevel", "run-grouplevel"]
    for step in steps:
        steponlygroup = stepgroup.add_mutually_exclusive_group(required=False)
        steponlygroup.add_argument(f"--{step}-only", action="store_true", default=False)
        steponlygroup.add_argument(f"--skip-{step}", action="store_true", default=False)
        if "run" not in step:
            steponlygroup.add_argument(f"--stop-after-{step}", action="store_true", default=False)

    workflowgroup = ap.add_argument_group("workflow", "")

    workflowgroup.add_argument("--nipype-omp-nthreads", type=int)
    workflowgroup.add_argument(
        "--skull-strip-algorithm",
        choices=["none", "ants"],
        default="ants",
        help="specify how to perform skull stripping",
    )
    workflowgroup.add_argument("--no-compose-transforms", action="store_true", default=False)
    workflowgroup.add_argument("--freesurfer", action="store_true", default=False)

    execgraphgroup = ap.add_argument_group("execgraph", "")
    execgraphgroup.add_argument("--workflow-file", type=str, help="manually select workflow file")
    chunkinggroup = execgraphgroup.add_mutually_exclusive_group(required=False)
    chunkinggroup.add_argument(
        "--n-chunks", type=int, help="number of subject-level workflow chunks to generate"
    )
    chunkinggroup.add_argument(
        "--subject-chunks",
        action="store_true",
        default=False,
        help="generate one subject-level workflow per subject",
    )

    rungroup = ap.add_argument_group("run", "")
    rungroup.add_argument("--execgraph-file", type=str, help="manually select execgraph file")
    rungroup.add_argument("--chunk-index", type=int, help="select which subjectlevel chunk to run")
    rungroup.add_argument("--nipype-memory-gb", type=float)
    rungroup.add_argument("--nipype-n-procs", type=int, default=cpu_count())
    rungroup.add_argument("--nipype-run-plugin", type=str, default="MultiProc")
    rungroup.add_argument(
        "--keep",
        choices=["all", "some", "none"],
        default="some",
        help="choose which intermediate files to keep",
    )

    ap.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="print the version number and exit",
        default=False,
    )

    args = ap.parse_args()
    global debug
    debug = args.debug
    config.execution.debug = debug
    verbose = args.verbose

    if args.version is True:
        sys.stdout.write(f"{__version__}\n")
        sys.exit(0)

    should_run = {step: True for step in steps}

    for step in steps:
        attrname = f"{step}-only".replace("-", "_")
        if getattr(args, attrname) is True:
            should_run = {step0: step0 == step for step0 in steps}
            break

    for step in steps:
        if "run" in step:
            continue
        attrname = f"stop-after-{step}".replace("-", "_")
        if getattr(args, attrname) is True:
            state = True
            for step0 in steps:
                should_run[step0] = state
                if step0 == step:
                    state = False
            break

    for step in steps:
        attrname = f"skip-{step}".replace("-", "_")
        if getattr(args, attrname) is True:
            should_run[step] = False

    workdir = args.workdir
    if workdir is not None:  # resolve workdir in fs_root
        abspath = op.abspath(workdir)
        if not abspath.startswith(args.fs_root):
            abspath = op.normpath(args.fs_root + abspath)
        workdir = abspath

    if should_run["spec-ui"]:
        from .ui import init_spec_ui
        from calamities.config import config as calamities_config

        calamities_config.fs_root = args.fs_root
        workdir = init_spec_ui(workdir=workdir, debug=debug)

    assert workdir is not None, "Missing working directory"
    assert op.isdir(workdir), "Working directory does not exist"

    import logging
    from .logger import Logger

    # if not Logger.is_setup:
    Logger.setup(workdir, debug=debug, verbose=verbose)
    logger = logging.getLogger("pipeline")

    logger.info(f"Version: {__version__}")
    logger.info(f"Debug: {debug}")

    if args.watchdog is True:
        from .watchdog import start_watchdog_daemon

        start_watchdog_daemon()

    if not should_run["spec-ui"]:
        logger.info(f"Did not run step: spec")

    workflow = None

    if not should_run["workflow"]:
        logger.info(f"Did not run step: workflow")
    else:
        logger.info(f"Running step: workflow")
        from .workflow import init_workflow

        if args.nipype_omp_nthreads is not None and args.nipype_omp_nthreads > 0:
            config.nipype.omp_nthreads = args.nipype_omp_nthreads
            logger.info(f"Using config.nipype.omp_nthreads={config.nipype.omp_nthreads} from args")
        else:
            config.nipype.omp_nthreads = (
                8 if args.nipype_n_procs > 16 else (4 if args.nipype_n_procs > 8 else 1)
            )
            logger.info(f"Inferred config.nipype.omp_nthreads={config.nipype.omp_nthreads}")

        workflow = init_workflow(
            workdir,
            no_compose_transforms=args.no_compose_transforms,
            freesurfer=args.freesurfer,
            skull_strip_algorithm=args.skull_strip_algorithm,
        )

    execgraphs = None

    if not should_run["execgraph"]:
        logger.info(f"Did not run step: execgraph")
    else:
        logger.info(f"Running step: execgraph")
        from .execgraph import init_execgraph

        if workflow is None:
            from .utils import loadpicklelzma

            assert (
                args.workflow_file is not None
            ), "Missing required --workflow-file input for step execgraph"
            workflow = loadpicklelzma(args.workflow_file)
            logger.info(f'Using workflow defined in file "{args.workflow_file}"')
        else:
            logger.info(f"Using workflow from previous step")

        execgraphs = init_execgraph(
            workdir, workflow, n_chunks=args.n_chunks, subject_chunks=args.subject_chunks
        )

    if (
        not should_run["run"]
        and not should_run["run-subjectlevel"]
        and not should_run["run-grouplevel"]
    ):
        logger.info(f"Did not run step: run")
    else:
        logger.info(f"Running step: run")
        if execgraphs is None:
            from .utils import loadpicklelzma

            assert (
                args.execgraph_file is not None
            ), "Missing required --execgraph-file input for step run"
            execgraphs = loadpicklelzma(args.execgraph_file)
            if not isinstance(execgraphs, list):
                execgraphs = [execgraphs]
            logger.info(f'Using execgraphs defined in file "{args.execgraph_file}"')
        else:
            logger.info(f"Using execgraphs from previous step")

        import nipype.pipeline.plugins as nip
        import pipeline.plugins as ppp

        plugin_args = {
            "workdir": workdir,
            "debug": debug,
            "verbose": verbose,
            "watchdog": args.watchdog,
            "stop_on_first_crash": debug,
            "raise_insufficient": False,
            "keep": args.keep,
        }
        if args.nipype_n_procs is not None:
            plugin_args["n_procs"] = args.nipype_n_procs
        if args.nipype_memory_gb is not None:
            plugin_args["memory_gb"] = args.nipype_memory_gb

        runnername = f"{args.nipype_run_plugin}Plugin"
        if hasattr(ppp, runnername):
            logger.info(f'Using a patched version of nipype_run_plugin "{runnername}"')
            runnercls = getattr(ppp, runnername)
        elif hasattr(nip, runnername):
            logger.warning(f'Using unsupported nipype_run_plugin "{runnername}"')
            runnercls = getattr(nip, runnername)
        else:
            raise ValueError(f'Unknown nipype_run_plugin "{runnername}"')
        runner = runnercls(plugin_args=plugin_args)

        execgraphstorun = []
        if len(execgraphs) > 1:
            n_subjectlevel_chunks = len(execgraphs) - 1
            if not should_run["run-subjectlevel"]:
                logger.info(f"Will not run subjectlevel chunks")
            elif args.chunk_index is not None:
                zerobasedchunkindex = args.chunk_index - 1
                assert zerobasedchunkindex < n_subjectlevel_chunks
                logger.info(
                    f"Will run subjectlevel chunk {args.chunk_index} of {n_subjectlevel_chunks}"
                )
                execgraphstorun.append(execgraphs[zerobasedchunkindex])
            else:
                logger.info(f"Will run all {n_subjectlevel_chunks} subjectlevel chunks")
                execgraphstorun.extend(execgraphs[:-1])

            if not should_run["run-grouplevel"]:
                logger.info(f"Will not run grouplevel chunk")
            else:
                logger.info(f"Will run grouplevel chunk")
                execgraphstorun.append(execgraphs[-1])
        elif len(execgraphs) == 1:
            execgraphstorun.append(execgraphs[0])
        else:
            raise ValueError("No execgraphs")

        n_execgraphstorun = len(execgraphstorun)
        for i, execgraph in enumerate(execgraphstorun):
            from .utils import first

            if len(execgraphs) > 1:
                logger.info(f"Running chunk {i+1} of {n_execgraphstorun}")
            runner.run(execgraph, updatehash=False, config=first(execgraph.nodes()).config)
            if len(execgraphs) > 1:
                logger.info(f"Completed chunk {i+1} of {n_execgraphstorun}")


def main():
    try:
        _main()
    except Exception as e:
        logger = logging.getLogger("pipeline")
        logger.exception("Exception: %s", e)

        global debug
        if debug:
            import pdb

            pdb.post_mortem()
