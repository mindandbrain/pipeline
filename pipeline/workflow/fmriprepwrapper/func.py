# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

import numpy as np

from nipype.pipeline import engine as pe
from nipype.interfaces import utility as niu
from nipype.interfaces.fsl import Split as FSLSplit

from fmriprep.workflows.bold import init_bold_confs_wf
from fmriprep.workflows.bold.confounds import init_carpetplot_wf
from fmriprep import config

from ..memory import MemoryCalculator
from ..utils import ConnectAttrlistHelper, make_reportnode, make_reportnode_datasink

from .sdc import init_sdc_estimate_wf

from niworkflows.interfaces.nibabel import ApplyMask
from niworkflows.interfaces.utility import KeySelect

from niworkflows.func.util import init_bold_reference_wf
from fmriprep.workflows.bold.hmc import init_bold_hmc_wf
from fmriprep.workflows.bold.registration import init_bold_t1_trans_wf, init_bold_reg_wf
from fmriprep.workflows.bold.resampling import (
    init_bold_std_trans_wf,
    init_bold_preproc_trans_wf,
)
from fmriprep.workflows.bold.confounds import init_ica_aroma_wf  # , init_carpetplot_wf

in_attrs_from_anat_preproc_wf = [
    "t1w_preproc",
    "t1w_mask",
    "t1w_dseg",
    "t1w_tpms",
    "anat2std_xfm",
    "std2anat_xfm",
    "template",
]
connect_func_wf_attrs_from_anat_preproc_wf = ConnectAttrlistHelper(in_attrs_from_anat_preproc_wf)

func_preproc_wf_out_attrs = [
    "bold_t1",
    "bold_t1_ref",
    "bold_mask_t1",
    "bold_aseg_t1",
    "bold_aparc_t1",
    "bold_std",
    "bold_std_ref",
    "bold_mask_std",
    "bold_aseg_std",
    "bold_aparc_std",
    "bold_native",
    "bold_cifti",
    "cifti_variant",
    "cifti_metadata",
    "cifti_density",
    "surfaces",
    "confounds",
    "aroma_noise_ics",
    "melodic_mix",
    "nonaggr_denoised_file",
    "confounds_metadata",
]


def init_func_preproc_wf(
    workdir=None, name="func_preproc_wf", fmap_type=None, memcalc=MemoryCalculator()
):
    """
    simplified from fmriprep/workflows/bold/base.py
    """

    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(
        niu.IdentityInterface(
            fields=["bold_file", "fmaps", "metadata", *in_attrs_from_anat_preproc_wf]
        ),
        name="inputnode",
    )

    outputnode = pe.Node(
        niu.IdentityInterface(
            fields=[
                *func_preproc_wf_out_attrs,
                "aroma_confounds",
                "aroma_metadata",
                "movpar_file",
                "rmsd_file",
                "skip_vols",
            ]
        ),
        name="outputnode",
    )

    def get_repetition_time(dic):
        return dic.get("RepetitionTime")

    metadatanode = pe.Node(niu.IdentityInterface(fields=["repetition_time"]), name="metadatanode")
    workflow.connect(
        [(inputnode, metadatanode, [(("metadata", get_repetition_time), "repetition_time")],)]
    )

    # Generate a brain-masked conversion of the t1w
    t1w_brain = pe.Node(ApplyMask(), name="t1w_brain")
    workflow.connect(
        [(inputnode, t1w_brain, [("t1w_preproc", "in_file"), ("t1w_mask", "in_mask")])]
    )

    # Generate a tentative boldref
    bold_reference_wf = init_bold_reference_wf(omp_nthreads=config.nipype.omp_nthreads)
    bold_reference_wf.get_node("inputnode").inputs.dummy_scans = config.workflow.dummy_scans
    workflow.connect(inputnode, "bold_file", bold_reference_wf, "inputnode.bold_file")
    workflow.connect(bold_reference_wf, "outputnode.skip_vols", outputnode, "skip_vols")

    # SDC (SUSCEPTIBILITY DISTORTION CORRECTION) or bypass ##########################
    bold_sdc_wf = init_sdc_estimate_wf(fmap_type=fmap_type)
    workflow.connect(
        [
            (
                inputnode,
                bold_sdc_wf,
                [("fmaps", "inputnode.fmaps"), ("metadata", "inputnode.metadata")],
            ),
            (
                bold_reference_wf,
                bold_sdc_wf,
                [
                    ("outputnode.ref_image", "inputnode.epi_file"),
                    ("outputnode.ref_image_brain", "inputnode.epi_brain"),
                    ("outputnode.bold_mask", "inputnode.epi_mask"),
                ],
            ),
        ]
    )

    # Top-level BOLD splitter
    bold_split = pe.Node(FSLSplit(dimension="t"), name="bold_split", mem_gb=memcalc.series_gb * 3)
    workflow.connect(inputnode, "bold_file", bold_split, "in_file")

    # HMC on the BOLD
    bold_hmc_wf = init_bold_hmc_wf(
        name="bold_hmc_wf", mem_gb=memcalc.series_gb, omp_nthreads=config.nipype.omp_nthreads,
    )
    workflow.connect(
        [
            (
                bold_reference_wf,
                bold_hmc_wf,
                [
                    ("outputnode.raw_ref_image", "inputnode.raw_ref_image"),
                    ("outputnode.bold_file", "inputnode.bold_file"),
                ],
            ),
            (
                bold_hmc_wf,
                outputnode,
                [("outputnode.movpar_file", "movpar_file"), ("outputnode.rmsd_file", "rmsd_file")],
            ),
        ]
    )

    # calculate BOLD registration to T1w
    bold_reg_wf = init_bold_reg_wf(
        freesurfer=config.workflow.run_reconall,
        use_bbr=config.workflow.use_bbr,
        bold2t1w_dof=config.workflow.bold2t1w_dof,
        bold2t1w_init=config.workflow.bold2t1w_init,
        mem_gb=memcalc.series_std_gb,
        omp_nthreads=config.nipype.omp_nthreads,
        use_compression=False,
    )
    workflow.connect(
        [
            (inputnode, bold_reg_wf, [("t1w_dseg", "inputnode.t1w_dseg")]),
            (t1w_brain, bold_reg_wf, [("out_file", "inputnode.t1w_brain")]),
            (bold_sdc_wf, bold_reg_wf, [("outputnode.epi_brain", "inputnode.ref_bold_brain")],),
        ]
    )

    # apply BOLD registration to T1w
    bold_t1_trans_wf = init_bold_t1_trans_wf(
        name="bold_t1_trans_wf",
        freesurfer=config.workflow.run_reconall,
        use_fieldwarp=fmap_type is not None,
        multiecho=False,
        mem_gb=memcalc.series_std_gb,
        omp_nthreads=config.nipype.omp_nthreads,
        use_compression=False,
    )
    workflow.connect(
        [
            (
                inputnode,
                bold_t1_trans_wf,
                [("bold_file", "inputnode.name_source"), ("t1w_mask", "inputnode.t1w_mask")],
            ),
            (
                bold_sdc_wf,
                bold_t1_trans_wf,
                [
                    ("outputnode.epi_brain", "inputnode.ref_bold_brain"),
                    ("outputnode.epi_mask", "inputnode.ref_bold_mask"),
                    ("outputnode.out_warp", "inputnode.fieldwarp"),
                ],
            ),
            (t1w_brain, bold_t1_trans_wf, [("out_file", "inputnode.t1w_brain")]),
            (bold_split, bold_t1_trans_wf, [("out_files", "inputnode.bold_split")]),
            (bold_hmc_wf, bold_t1_trans_wf, [("outputnode.xforms", "inputnode.hmc_xforms")],),
            (
                bold_reg_wf,
                bold_t1_trans_wf,
                [("outputnode.itk_bold_to_t1", "inputnode.itk_bold_to_t1")],
            ),
        ]
    )

    # Apply transforms in 1 shot
    # Only use uncompressed output if AROMA is to be run
    bold_bold_trans_wf = init_bold_preproc_trans_wf(
        mem_gb=memcalc.series_std_gb,
        omp_nthreads=config.nipype.omp_nthreads,
        use_compression=not config.execution.low_mem,
        use_fieldwarp=True,
        name="bold_bold_trans_wf",
    )
    # bold_bold_trans_wf.inputs.inputnode.name_source = ref_file
    workflow.connect(
        [
            (inputnode, bold_bold_trans_wf, [("bold_file", "inputnode.name_source")]),
            (bold_split, bold_bold_trans_wf, [("out_files", "inputnode.bold_file")]),
            (bold_hmc_wf, bold_bold_trans_wf, [("outputnode.xforms", "inputnode.hmc_xforms")],),
            (
                bold_sdc_wf,
                bold_bold_trans_wf,
                [
                    ("outputnode.out_warp", "inputnode.fieldwarp"),
                    ("outputnode.epi_mask", "inputnode.bold_mask"),
                ],
            ),
        ]
    )

    # Apply transforms in 1 shot
    # Only use uncompressed output if AROMA is to be run
    bold_std_trans_wf = init_bold_std_trans_wf(
        freesurfer=config.workflow.run_reconall,
        mem_gb=memcalc.series_std_gb,
        omp_nthreads=config.nipype.omp_nthreads,
        spaces=config.workflow.spaces,
        name="bold_std_trans_wf",
        use_compression=not config.execution.low_mem,
        use_fieldwarp=fmap_type is not None,
    )
    workflow.connect(
        [
            (
                inputnode,
                bold_std_trans_wf,
                [
                    ("template", "inputnode.templates"),
                    ("anat2std_xfm", "inputnode.anat2std_xfm"),
                    ("bold_file", "inputnode.name_source"),
                ],
            ),
            (bold_split, bold_std_trans_wf, [("out_files", "inputnode.bold_split")]),
            (bold_hmc_wf, bold_std_trans_wf, [("outputnode.xforms", "inputnode.hmc_xforms")],),
            (
                bold_reg_wf,
                bold_std_trans_wf,
                [("outputnode.itk_bold_to_t1", "inputnode.itk_bold_to_t1")],
            ),
            (
                bold_bold_trans_wf,
                bold_std_trans_wf,
                [("outputnode.bold_mask", "inputnode.bold_mask")],
            ),
            (bold_sdc_wf, bold_std_trans_wf, [("outputnode.out_warp", "inputnode.fieldwarp")],),
            (
                bold_std_trans_wf,
                outputnode,
                [
                    ("outputnode.bold_std", "bold_std"),
                    ("outputnode.bold_std_ref", "bold_std_ref"),
                    ("outputnode.bold_mask_std", "bold_mask_std"),
                ],
            ),
        ]
    )

    ica_aroma_wf = init_ica_aroma_wf(
        mem_gb=memcalc.series_std_gb,
        metadata={"RepetitionTime": np.nan},
        omp_nthreads=config.nipype.omp_nthreads,
        use_fieldwarp=True,
        err_on_aroma_warn=config.workflow.aroma_err_on_warn,
        aroma_melodic_dim=config.workflow.aroma_melodic_dim,
        name="ica_aroma_wf",
    )
    ica_aroma_wf.get_node("ica_aroma").inputs.denoise_type = "no"
    ica_aroma_wf.remove_nodes([ica_aroma_wf.get_node("add_nonsteady")])
    workflow.connect(
        [
            (inputnode, ica_aroma_wf, [("bold_file", "inputnode.name_source")]),
            (
                metadatanode,
                ica_aroma_wf,
                [("repetition_time", "melodic.tr_sec",), ("repetition_time", "ica_aroma.TR")],
            ),
            (bold_hmc_wf, ica_aroma_wf, [("outputnode.movpar_file", "inputnode.movpar_file")]),
            (bold_reference_wf, ica_aroma_wf, [("outputnode.skip_vols", "inputnode.skip_vols")]),
            (
                bold_std_trans_wf,
                ica_aroma_wf,
                [
                    ("outputnode.bold_std", "inputnode.bold_std"),
                    ("outputnode.bold_mask_std", "inputnode.bold_mask_std"),
                    ("outputnode.spatial_reference", "inputnode.spatial_reference"),
                ],
            ),
            (
                ica_aroma_wf,
                outputnode,
                [
                    ("outputnode.aroma_noise_ics", "aroma_noise_ics"),
                    ("outputnode.melodic_mix", "melodic_mix"),
                    ("outputnode.nonaggr_denoised_file", "nonaggr_denoised_file"),
                    ("outputnode.aroma_confounds", "aroma_confounds"),
                    ("outputnode.aroma_metadata", "aroma_metadata"),
                ],
            ),
        ]
    )

    bold_confounds_wf = init_bold_confs_wf(
        mem_gb=memcalc.series_std_gb,
        metadata={},
        regressors_all_comps=config.workflow.regressors_all_comps,
        regressors_fd_th=config.workflow.regressors_fd_th,
        regressors_dvars_th=config.workflow.regressors_dvars_th,
        name="bold_confounds_wf",
    )
    bold_confounds_wf.get_node("inputnode").inputs.t1_transform_flags = [False]
    workflow.connect(
        [
            (
                inputnode,
                bold_confounds_wf,
                [("t1w_tpms", "inputnode.t1w_tpms"), ("t1w_mask", "inputnode.t1w_mask")],
            ),
            (
                bold_hmc_wf,
                bold_confounds_wf,
                [
                    ("outputnode.movpar_file", "inputnode.movpar_file"),
                    ("outputnode.rmsd_file", "inputnode.rmsd_file"),
                ],
            ),
            (
                bold_reg_wf,
                bold_confounds_wf,
                [("outputnode.itk_t1_to_bold", "inputnode.t1_bold_xform")],
            ),
            (
                bold_reference_wf,
                bold_confounds_wf,
                [("outputnode.skip_vols", "inputnode.skip_vols")],
            ),
            (bold_confounds_wf, outputnode, [("outputnode.confounds_file", "confounds")]),
            (
                bold_confounds_wf,
                outputnode,
                [("outputnode.confounds_metadata", "confounds_metadata")],
            ),
            (
                metadatanode,
                bold_confounds_wf,
                [
                    ("repetition_time", "acompcor.repetition_time"),
                    ("repetition_time", "tcompcor.repetition_time"),
                ],
            ),
            (
                bold_bold_trans_wf,
                bold_confounds_wf,
                [
                    ("outputnode.bold", "inputnode.bold"),
                    ("outputnode.bold_mask", "inputnode.bold_mask"),
                ],
            ),
        ]
    )

    carpetplot_wf = init_carpetplot_wf(
        mem_gb=memcalc.series_std_gb,
        metadata={"RepetitionTime": np.nan},
        cifti_output=config.workflow.cifti_output,
        name="carpetplot_wf",
    )
    carpetplot_select_std = pe.Node(
        KeySelect(fields=["std2anat_xfm"], key="MNI152NLin2009cAsym"),
        name="carpetplot_select_std",
        run_without_submitting=True,
    )

    workflow.connect(
        [
            (
                inputnode,
                carpetplot_select_std,
                [("std2anat_xfm", "std2anat_xfm"), ("template", "keys")],
            ),
            (carpetplot_select_std, carpetplot_wf, [("std2anat_xfm", "inputnode.std2anat_xfm")],),
            (
                bold_bold_trans_wf,
                carpetplot_wf,
                [
                    ("outputnode.bold", "inputnode.bold"),
                    ("outputnode.bold_mask", "inputnode.bold_mask"),
                ],
            ),
            (
                bold_reg_wf,
                carpetplot_wf,
                [("outputnode.itk_t1_to_bold", "inputnode.t1_bold_xform")],
            ),
            (
                bold_confounds_wf,
                carpetplot_wf,
                [("outputnode.confounds_file", "inputnode.confounds_file")],
            ),
            (metadatanode, carpetplot_wf, [("repetition_time", "conf_plot.tr")]),
        ]
    )

    # Custom

    make_reportnode(workflow)
    assert workdir is not None
    make_reportnode_datasink(workflow, workdir)

    return workflow
