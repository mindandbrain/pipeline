# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from .taskbased import init_taskbased_wf
from .atlasbasedconnectivity import init_atlasbasedconnectivity_wf
from .seedbasedconnectivity import init_seedbasedconnectivity_wf
from .dualregression import init_dualregression_wf
from .reho import init_reho_wf
from .falff import init_falff_wf
from .imageoutput import init_imageoutput_wf

from ..memory import MemoryCalculator
from ...spec import Analysis


def init_firstlevel_analysis_wf(analysis=None, memcalc=MemoryCalculator()):
    assert isinstance(analysis, Analysis)

    if analysis.type == "image_output":
        return init_imageoutput_wf(analysis=analysis, memcalc=memcalc)
    elif analysis.type == "task_based":
        return init_taskbased_wf(analysis=analysis, memcalc=memcalc)
    elif analysis.type == "seed_based_connectivity":
        return init_seedbasedconnectivity_wf(analysis=analysis, memcalc=memcalc)
    elif analysis.type == "dual_regression":
        return init_dualregression_wf(analysis=analysis, memcalc=memcalc)
    elif analysis.type == "atlas_based_connectivity":
        return init_atlasbasedconnectivity_wf(analysis=analysis, memcalc=memcalc)
    elif analysis.type == "reho":
        return init_reho_wf(analysis=analysis, memcalc=memcalc)
    elif analysis.type == "falff":
        return init_falff_wf(analysis=analysis, memcalc=memcalc)
    else:
        raise ValueError(f'Unknown analysis.type "{analysis.type}"')


def connect_firstlevel_analysis_extra_args(analysisworkflow, analysis, database, boldfile):
    inputnode = analysisworkflow.get_node("inputnode")
    if analysis.type == "task_based":
        condition_files = database.get_associations(boldfile, datatype="func", suffix="events")
        if "txt" in database.get_tagval_set("extension", filepaths=condition_files):
            condition_files = [
                (condition_file, database.get_tagval(condition_file, "condition"))
                for condition_file in condition_files
            ]
        inputnode.inputs.condition_files = condition_files
    elif analysis.type == "seed_based_connectivity":
        if analysis.tags.seed is None:
            seed_files = list(database.get_all_with_tag("seed"))
        else:
            seed_files = list(database.get(seed=analysis.tags.seed))
        assert len(seed_files) > 0, "No seed files"
        seed_names = database.get_tagval(seed_files, "seed")
        inputnode.inputs.seed_files = seed_files
        inputnode.inputs.seed_names = seed_names
    elif analysis.type == "dual_regression":
        if analysis.tags.map is None:
            map_files = list(database.get_all_with_tag("map"))
        else:
            map_files = list(database.get(map=analysis.tags.map))
        assert len(map_files) > 0, "No map files"
        map_tags = database.get_tagval(map_files, "map")
        map_components = [map_tagobj.components for map_tagobj in map_tags]
        inputnode.inputs.map_files = map_files
        inputnode.inputs.map_components = map_components
    elif analysis.type == "atlas_based_connectivity":
        if analysis.tags.atlas is None:
            atlas_files = list(database.get_all_with_tag("atlas"))
        else:
            atlas_files = list(database.get(atlas=analysis.tags.atlas))
        assert len(atlas_files) > 0, "No atlas files"
        atlas_names = database.get_tagval(atlas_files, "atlas")
        inputnode.inputs.atlas_files = atlas_files
        inputnode.inputs.atlas_names = atlas_names
