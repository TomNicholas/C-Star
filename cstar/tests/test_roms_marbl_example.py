import builtins
from pathlib import Path
import yaml
from contextlib import contextmanager

import pytest

import cstar


# TODO this assumes you are running pytest from the root directory of the cloned repo, which is fragile
ROMS_MARBL_BASE_BLUEPRINT_PATH = "./examples/cstar_blueprint_roms_marbl_example.yaml"


@pytest.fixture
def roms_marbl_blueprint_remote_datasets(tmp_path) -> Path:
    """Creates blueprint yaml file in temporary directory"""

    with open(ROMS_MARBL_BASE_BLUEPRINT_PATH, "r") as file:
        blueprint_dict = yaml.load(file, Loader=yaml.Loader)

    blueprint_filepath = tmp_path / "blueprint.yaml"

    with open(blueprint_filepath, "w") as file:
        yaml.dump(blueprint_dict, file)

    return blueprint_filepath


# TODO similar fixture but the blueprint file contains paths to local files instead
# TODO should these local files be in a given temporary directory?


class TestRomsMarbl:
    def test_roms_marbl_remote_files(
        self, tmpdir, mock_user_input, roms_marbl_blueprint_remote_datasets
    ):
        """Test using URLs to point to input datasets"""

        roms_marbl_remote_case = cstar.Case.from_blueprint(
            blueprint=roms_marbl_blueprint_remote_datasets,
            caseroot=tmpdir,
            start_date="20120103 12:00:00",
            end_date="20120103 12:30:00",
        )

        with mock_user_input("y"):
            # do we actually need user input for all these steps?
            roms_marbl_remote_case.setup()

            # why are we persisting this blueprint file then not using it again in the test?
            #roms_marbl_remote_case.persist(tmpdir / "test_blueprint.yaml")

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
