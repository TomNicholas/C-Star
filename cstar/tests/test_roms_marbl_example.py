from pathlib import Path
import yaml
from typing import Callable

import pytest

import cstar


# TODO this assumes you are running pytest from the root directory of the cloned repo, which is fragile
# TODO move this somewhere more top-level
EXAMPLE_BLUEPRINTS = {
    "ROMS_MARBL_BASE": "./examples/cstar_blueprint_roms_marbl_example.yaml",
}


@pytest.fixture
def blueprint_as_dict() -> Callable[[str], dict]:
    """Given the name of a pre-defined blueprinm, return it as an in-memory dict."""

    def _base_blueprint_dict(name: str) -> dict:
        base_blueprint_path = EXAMPLE_BLUEPRINTS[name]

        with open(base_blueprint_path, "r") as file:
            base_blueprint_dict = yaml.load(file, Loader=yaml.Loader)

        return base_blueprint_dict

    return _base_blueprint_dict


@pytest.fixture
def blueprint_as_path(blueprint_as_dict, tmp_path) -> Callable[[str], Path]:
    """Given the name of a pre-defined blueprint, returns it as a (temporary) path to an on-disk file."""

    def _blueprint_as_path(name: str) -> Path:
        blueprint_dict = blueprint_as_dict(name)

        # save the blueprint to a temporary path
        blueprint_filepath = tmp_path / "blueprint.yaml"
        with open(blueprint_filepath, "w") as file:
            yaml.dump(blueprint_dict, file)

        return blueprint_filepath

    return _blueprint_as_path


# TODO fixture supplying blueprint file containing paths to local files / additional code instead
# TODO this should be implemented by altering the in-memory dict before returning
# TODO then using the other fixture to write that altered dict to a temporary directory
# TODO should these local files be in a specified temporary directory?

# TODO move these fixtures to dedicated modules


class TestRomsMarbl:
    def test_roms_marbl_remote_files(self, tmpdir, mock_user_input, blueprint_as_path):
        """Test using URLs to point to input datasets"""

        roms_marbl_base_blueprint_filepath = blueprint_as_path("ROMS_MARBL_BASE")

        roms_marbl_remote_case = cstar.Case.from_blueprint(
            blueprint=roms_marbl_base_blueprint_filepath,
            caseroot=tmpdir,
            start_date="20120103 12:00:00",
            end_date="20120103 12:30:00",
        )

        with mock_user_input("y"):
            # do we actually need user input for all these steps?
            roms_marbl_remote_case.setup()

            # why are we persisting this blueprint file then not using it again in the test?
            # roms_marbl_remote_case.persist(tmpdir / "test_blueprint.yaml")

            roms_marbl_remote_case.build()
            roms_marbl_remote_case.pre_run()
            roms_marbl_remote_case.run()
            roms_marbl_remote_case.post_run()

    @pytest.mark.skip(reason="not yet implemented")
    def test_roms_marbl_local_files(self, tmpdir, roms_marbl_blueprint_local_datasets):
        """Test using available local input datasets"""

        # TODO have a fixture that downloads the files to a temporary directory
        # Does that basically just mean running case.setup()?
        ...
