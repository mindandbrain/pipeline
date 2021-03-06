# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

import nipype.algorithms.modelgen as model

from nipype.pipeline import engine as pe
from nipype.interfaces import utility as niu
from nipype.interfaces import fsl

from ...interface import MakeDofVolume, MakeResultdicts, ParseConditionFile
from ...utils import ravel, first, firstfloat
from ...spec import (
    Tags,
    Analysis,
    BandPassFilteredTag,
    ConfoundsRemovedTag,
    SmoothedTag,
    GrandMeanScaledTag,
)

from ..memory import MemoryCalculator


def init_taskbased_wf(analysis=None, memcalc=MemoryCalculator()):
    """
    create workflow to calculate a first level glm for task functional data
    """

    assert isinstance(analysis, Analysis)
    assert isinstance(analysis.tags, Tags)

    # make bold file variant specification
    boldfilefields = ["bold_file"]
    varianttupls = [("space", analysis.tags.space)]
    if analysis.tags.grand_mean_scaled is not None:
        assert isinstance(analysis.tags.grand_mean_scaled, GrandMeanScaledTag)
        varianttupls.append(analysis.tags.grand_mean_scaled.as_tupl())
    if analysis.tags.band_pass_filtered is not None:
        assert isinstance(analysis.tags.band_pass_filtered, BandPassFilteredTag)
        assert analysis.tags.band_pass_filtered.type == "gaussian"
        varianttupls.append(analysis.tags.band_pass_filtered.as_tupl())
    if analysis.tags.confounds_removed is not None:
        assert isinstance(analysis.tags.confounds_removed, ConfoundsRemovedTag)
        confounds_removed_names = tuple(
            name for name in analysis.tags.confounds_removed.names if "aroma_motion" in name
        )
        varianttupls.append(("confounds_removed", confounds_removed_names))
        confounds_extract_names = tuple(
            name for name in analysis.tags.confounds_removed.names if "aroma_motion" not in name
        )
        if len(confounds_extract_names) > 0:
            boldfilefields.append("confounds_file")
            varianttupls.append(("confounds_extract", confounds_extract_names))
    if analysis.tags.smoothed is not None:
        assert isinstance(analysis.tags.smoothed, SmoothedTag)
        varianttupls.append(analysis.tags.smoothed.as_tupl())
    variantdict = dict(varianttupls)

    boldfilevariant = (tuple(boldfilefields), tuple(varianttupls))

    assert analysis.name is not None
    workflow = pe.Workflow(name=analysis.name)

    # inputs are the bold file, the mask file and the confounds file
    inputnode = pe.Node(
        niu.IdentityInterface(fields=[*boldfilefields, "mask_file", "condition_files", "metadata"]),
        name="inputnode",
    )

    # parse condition files into three (ordered) lists
    parseconditionfile = pe.Node(interface=ParseConditionFile(), name="parseconditionfile",)
    workflow.connect(inputnode, "condition_files", parseconditionfile, "in_any")

    def get_repetition_time(dic):
        return dic.get("RepetitionTime")

    # first level model specification
    modelspec = pe.Node(interface=model.SpecifyModel(input_units="secs",), name="modelspec",)
    workflow.connect(
        [
            (
                inputnode,
                modelspec,
                [
                    ("bold_file", "functional_runs"),
                    (("metadata", get_repetition_time), "time_repetition"),
                ],
            ),
            (parseconditionfile, modelspec, [("subject_info", "subject_info")]),
        ]
    )
    if "band_pass_filtered" in variantdict:
        modelspec.inputs.high_pass_filter_cutoff = float(analysis.tags.band_pass_filtered.high)
    if "confounds_extract" in variantdict:
        workflow.connect([(inputnode, modelspec, [("confounds_file", "realignment_parameters")])])

    # transform contrasts dictionary to nipype list data structure
    contrasts = [
        [contrast.name, contrast.type.upper(), *map(list, zip(*contrast.values.items()))]
        for contrast in analysis.contrasts
    ]

    # generate design from first level specification
    level1design = pe.Node(
        interface=fsl.Level1Design(
            contrasts=contrasts,
            model_serial_correlations=True,
            bases={"dgamma": {"derivs": False}},
        ),
        name="level1design",
    )
    workflow.connect(
        [
            (inputnode, level1design, [(("metadata", get_repetition_time), "interscan_interval")],),
            (modelspec, level1design, [("session_info", "session_info")]),
        ]
    )

    # generate required input files for FILMGLS from design
    modelgen = pe.Node(
        interface=fsl.FEATModel(), name="modelgen", iterfield=["fsf_file", "ev_files"]
    )
    workflow.connect(
        [(level1design, modelgen, [("fsf_files", "fsf_file"), ("ev_files", "ev_files")],)]
    )

    # calculate range of image values to determine cutoff value
    # for FILMGLS
    boldfilecutoff = pe.Node(interface=fsl.ImageStats(op_string="-R"), name="boldfilecutoff")
    workflow.connect([(inputnode, boldfilecutoff, [("bold_file", "in_file")])])

    # actually estimate the first level model
    modelestimate = pe.Node(
        interface=fsl.FILMGLS(smooth_autocorr=True, mask_size=5),
        name="modelestimate",
        iterfield=["design_file", "in_file", "tcon_file"],
    )
    workflow.connect(
        [
            (inputnode, modelestimate, [("bold_file", "in_file")]),
            (boldfilecutoff, modelestimate, [(("out_stat", firstfloat), "threshold")]),
            (modelgen, modelestimate, [("design_file", "design_file"), ("con_file", "tcon_file")],),
        ]
    )

    # make dof volume
    makedofvolume = pe.MapNode(
        interface=MakeDofVolume(), iterfield=["dof_file", "cope_file"], name="makedofvolume",
    )
    workflow.connect(
        [
            (
                modelestimate,
                makedofvolume,
                [(("copes", first), "cope_file"), ("dof_file", "dof_file")],
            ),
        ]
    )

    outputnode = pe.Node(
        interface=MakeResultdicts(
            keys=[
                "firstlevelanalysisname",
                "firstlevelfeaturename",
                "cope",
                "varcope",
                "zstat",
                "dof_file",
                "mask_file",
            ]
        ),
        name="outputnode",
    )
    outputnode.inputs.firstlevelanalysisname = analysis.name
    outputnode.inputs.firstlevelfeaturename = list(map(first, contrasts))
    workflow.connect(
        [
            (inputnode, outputnode, [("metadata", "basedict"), ("mask_file", "mask_file")]),
            (
                modelestimate,
                outputnode,
                [
                    (("copes", ravel), "cope"),
                    (("varcopes", ravel), "varcope"),
                    (("zstats", ravel), "zstat"),
                ],
            ),
            (makedofvolume, outputnode, [("out_file", "dof_file")]),
        ]
    )

    return workflow, (boldfilevariant,)
