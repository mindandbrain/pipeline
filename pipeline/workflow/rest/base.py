# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from os import path as op

from nipype.pipeline import engine as pe
from nipype.interfaces import utility as niu

from ..confounds import init_confoundsregression_wf
from ..temporalfilter import init_bandpass_wf
from .brainatlas import init_brainatlas_wf
from .seedconnectivity import init_seedconnectivity_wf
from .dualregression import init_dualregression_wf
from .reho import init_reho_wf
from .alff import init_alff_wf

from ..utils import make_outputnode


def init_rest_wf(metadata,
                 name="rest"):

    workflow = pe.Workflow(name=name)

    unfilteredFileEndpoint = [inputnode, "bold_file"]

    # inputs are the bold file, the mask file and the confounds file
    inputnode = pe.Node(niu.IdentityInterface(
        fields=["bold_file", "mask_file", "confounds"]),
        name="inputnode"
    )

    confoundsregression_wf = init_confoundsregression_wf(metadata)
    workflow.connect(
        *unfilteredFileEndpoint,
        confoundsregression_wf, "inputnode.bold_file"
    )

    workflow.connect([
        (inputnode, confoundsregression_wf, [
            ("mask_file", "inputnode.mask_file"),
            ("confounds", "inputnode.confounds"),
        ])
    ])

    confoundsFilteredFileEndpoint = unfilteredFileEndpoint
    if confoundsregression_wf is not None:
        confoundsFilteredFileEndpoint = \
            [confoundsregression_wf, "outputnode.filtered_file"]

    repetition_time = metadata["RepetitionTime"]

    bandpass_wf = init_bandpass_wf(repetition_time)
    workflow.connect(
        *confoundsFilteredFileEndpoint,
        bandpass_wf, "inputnode.bold_file"
    )
    bandpassFilteredFileEndpoint = [bandpass_wf, "outputnode.filtered_file"]

    workflow.connect([
        (inputnode, bandpass_wf, [
            ("mask_file", "inputnode.mask_file"),
        ])
    ])

    outByWorkflowName = {}

    def aggregate(out):
        wf, outnames, outfields = out

        if len(outnames) == 0:
            return

        outByWorkflowName[wf.name] = out

    out = init_brainatlas_wf(metadata)
    aggregate(out)
    brainatlas_wf, outnames, _ = out
    if len(outnames) > 0:
        workflow.connect(
            *confoundsFilteredFileEndpoint,
            brainatlas_wf, "inputnode.bold_file"
        )

    out = init_seedconnectivity_wf(metadata)
    aggregate(out)
    seedconnectivity_wf, outnames, _ = out
    if len(outnames) > 0:
        workflow.connect(
            *unfilteredFileEndpoint,
            seedconnectivity_wf, "inputnode.bold_file"
        )

    if "ICAMaps" in metadata:
        for name, componentsfile in metadata["ICAMaps"].items():
            out = init_dualregression_wf(
                metadata,
                componentsfile,
                name="{}_dualregression_wf".format(name)
            )
            aggregate(out)
            dualregression_wf, outnames, _ = out
            if len(outnames) > 0:
                workflow.connect(
                    *unfilteredFileEndpoint,
                    dualregression_wf, "inputnode.bold_file"
                )

    if "reho" in metadata and metadata["reho"]:
        out = init_reho_wf()
        aggregate(out)
        reho_wf, outnames, _ = out
        if len(outnames) > 0:
            workflow.connect(
                *bandpassFilteredFileEndpoint,
                reho_wf, "inputnode.bold_file"
            )

    if "alff" in metadata and metadata["alff"]:
        out = init_alff_wf()
        aggregate(out)
        alff_wf, outnames, _ = out
        if len(outnames) > 0:
            workflow.connect(
                *confoundsFilteredFileEndpoint,
                alff_wf, "inputnode.bold_file"
            )
            workflow.connect(
                *bandpassFilteredFileEndpoint,
                alff_wf, "inputnode.filtered_file"
            )

    for workflowName, (wf, outnames, outfields) in outByWorkflowName.items():
        workflow.connect([
            (inputnode, wf, [
                ("mask_file", "inputnode.mask_file"),
                ("confounds", "inputnode.confounds"),
            ])
        ])

    _, outfieldsByOutname = make_outputnode(
        workflow, outByWorkflowName
    )

    outnames = list(outfieldsByOutname.keys())

    return workflow, outnames, outfieldsByOutname
