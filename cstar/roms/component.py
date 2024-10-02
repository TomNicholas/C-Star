import warnings
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, TYPE_CHECKING, List

from cstar.base.utils import _calculate_node_distribution, _replace_text_in_file
from cstar.base.component import Component, Discretization
from cstar.roms.base_model import ROMSBaseModel
from cstar.roms.input_dataset import (
    ROMSInputDataset,
    ROMSInitialConditions,
    ROMSModelGrid,
    ROMSSurfaceForcing,
    ROMSBoundaryForcing,
    ROMSTidalForcing,
)
from cstar.base.additional_code import AdditionalCode

from cstar.base.environment import (
    _CSTAR_COMPILER,
    _CSTAR_SCHEDULER,
    _CSTAR_SYSTEM,
    _CSTAR_SYSTEM_MAX_WALLTIME,
    _CSTAR_SYSTEM_DEFAULT_PARTITION,
    _CSTAR_SYSTEM_CORES_PER_NODE,
)

if TYPE_CHECKING:
    from cstar.roms import ROMSBaseModel


class ROMSComponent(Component):
    """
    An implementation of the Component class for the UCLA Regional Ocean Modeling System

    This subclass contains ROMS-specific implementations of the build(), pre_run(), run(), and post_run() methods.

    Attributes:
    -----------
    base_model: ROMSBaseModel
        An object pointing to the unmodified source code of ROMS at a specific commit
    namelists: AdditionalCode (Optional, default None)
        Namelist files contributing to a unique instance of the base model,
        to be used at runtime
    additional_source_code: AdditionalCode (Optional, default None)
        Additional source code contributing to a unique instance of a base model,
        to be included at compile time
    discretization: ROMSDiscretization
        Any information related to discretization of this ROMSComponent
        e.g. time step, number of levels, number of CPUs following each direction, etc.
    model_grid: ROMSModelGrid, optional
        The model grid InputDataset associated with this ROMSComponent
    initial_conditions: ROMSInitialConditions, optional
        The initial conditions InputDataset associated with this ROMSComponent
    tidal_forcing: ROMSTidalForcing, optional
        The tidal forcing InputDataset associated with this ROMSComponent
    surface_forcing: (list of) ROMSSurfaceForcing, optional
        list of surface forcing InputDataset objects associated with this ROMSComponent
    boundary_forcing: (list of) ROMSBoundaryForcing, optional
        list of boundary forcing InputDataset objects associated with this ROMSComponent


    Properties:
    -----------
    component_type: str
       The type of Component, in this case "ROMS"
    input_datasets: list
       A list of any input datasets associated with this instance of ROMSComponent

    Methods:
    --------
    build()
        Compiles any code associated with this configuration of ROMS
    pre_run()
        Performs pre-processing steps, such as partitioning input netcdf datasets into one file per core
    run()
        Runs the executable created by `build()`
    post_run()
        Performs post-processing steps, such as joining output netcdf files that are produced one-per-core

    """

    base_model: "ROMSBaseModel"
    additional_code: "AdditionalCode"
    discretization: "ROMSDiscretization"

    def __init__(
        self,
        base_model: "ROMSBaseModel",
        discretization: "ROMSDiscretization",
        namelists: "AdditionalCode",
        additional_source_code: "AdditionalCode",
        model_grid: Optional["ROMSModelGrid"] = None,
        initial_conditions: Optional["ROMSInitialConditions"] = None,
        tidal_forcing: Optional["ROMSTidalForcing"] = None,
        boundary_forcing: Optional[list["ROMSBoundaryForcing"]] = None,
        surface_forcing: Optional[list["ROMSSurfaceForcing"]] = None,
    ):
        """
        Initialize a ROMSComponent object from a ROMSBaseModel object, code, input datasets, and discretization information

        Parameters:
        -----------
        base_model: ROMSBaseModel
            An object pointing to the unmodified source code of a model handling an individual
            aspect of the simulation such as biogeochemistry or ocean circulation
        namelists: AdditionalCode (Optional, default None)
            Namelist files contributing to a unique instance of the base model,
            to be used at runtime
        additional_source_code: AdditionalCode (Optional, default None)
            Additional source code contributing to a unique instance of a base model,
            to be included at compile time
        discretization: ROMSDiscretization
            Any information related to discretization of this ROMSComponent
            e.g. time step, number of levels, number of CPUs following each direction, etc.
        model_grid: ROMSModelGrid, optional
            The model grid InputDataset associated with this ROMSComponent
        initial_conditions: ROMSInitialConditions, optional
            The initial conditions InputDataset associated with this ROMSComponent
        tidal_forcing: ROMSTidalForcing, optional
            The tidal forcing InputDataset associated with this ROMSComponent
        surface_forcing: (list of) ROMSSurfaceForcing, optional
            list of surface forcing InputDataset objects associated with this ROMSComponent
        boundary_forcing: (list of) ROMSBoundaryForcing, optional
            list of boundary forcing InputDataset objects associated with this ROMSComponent

        Returns:
        --------
        ROMSComponent:
            An intialized ROMSComponent object
        """

        self.base_model = base_model
        self.namelists = namelists
        self.additional_source_code = additional_source_code
        self.discretization = discretization
        self.model_grid = model_grid
        self.initial_conditions = initial_conditions
        self.tidal_forcing = tidal_forcing
        self.surface_forcing = [] if surface_forcing is None else surface_forcing
        self.boundary_forcing = [] if boundary_forcing is None else boundary_forcing

        # roms-specific
        self.exe_path: Optional[Path] = None
        self.partitioned_files: List[Path] | None = None

    @classmethod
    def from_dict(cls, component_dict):
        """
        Construct a ROMSComponent instance from a dictionary of kwargs.

        Parameters:
        -----------
        component_dict (dict):
           A dictionary of keyword arguments used to construct this component.

        Returns:
        --------
        ROMSComponent
           An initialized ROMSComponent object
        """

        component_kwargs = {}
        # Construct the BaseModel instance
        base_model_kwargs = component_dict.get("base_model")
        if base_model_kwargs is None:
            raise ValueError(
                "Cannot construct a ROMSComponent instance without a "
                + "ROMSBaseModel object, but could not find 'base_model' entry"
            )
        base_model = ROMSBaseModel(**base_model_kwargs)

        component_kwargs["base_model"] = base_model

        # Construct the Discretization instance
        discretization_kwargs = component_dict.get("discretization")
        if discretization_kwargs is None:
            raise ValueError(
                "Cannot construct a ROMSComponent instance without a "
                + "ROMSDiscretization object, but could not find 'discretization' entry"
            )
        discretization = ROMSDiscretization(**discretization_kwargs)

        component_kwargs["discretization"] = discretization

        # Construct any AdditionalCode instance associated with namelists
        namelists_kwargs = component_dict.get("namelists")
        if namelists_kwargs is None:
            raise ValueError(
                "Cannot construct a ROMSComponent instance without a runtime "
                + "namelist, but could not find 'namelists' entry"
            )
        namelists = AdditionalCode(**namelists_kwargs)
        component_kwargs["namelists"] = namelists

        # Construct any AdditionalCode instance associated with source mods
        additional_source_code_kwargs = component_dict.get("additional_source_code")
        if additional_source_code_kwargs is None:
            raise NotImplementedError(
                "This version of C-Star does not support ROMSComponent instances "
                + "without code to be included at compile time (.opt files, etc.), but "
                + "could not find an 'additional_source_code' entry."
            )

        additional_source_code = AdditionalCode(**additional_source_code_kwargs)
        component_kwargs["additional_source_code"] = additional_source_code

        # Construct any ROMSModelGrid instance:
        model_grid_kwargs = component_dict.get("model_grid")
        if model_grid_kwargs is not None:
            component_kwargs["model_grid"] = ROMSModelGrid(**model_grid_kwargs)

        # Construct any ROMSInitialConditions instance:
        initial_conditions_kwargs = component_dict.get("initial_conditions")
        if initial_conditions_kwargs is not None:
            component_kwargs["initial_conditions"] = ROMSInitialConditions(
                **initial_conditions_kwargs
            )

        # Construct any ROMSTidalForcing instance:
        tidal_forcing_kwargs = component_dict.get("tidal_forcing")
        if tidal_forcing_kwargs is not None:
            component_kwargs["tidal_forcing"] = ROMSTidalForcing(**tidal_forcing_kwargs)

        # Construct any ROMSBoundaryForcing instances:
        boundary_forcing_entries = component_dict.get("boundary_forcing", [])
        if len(boundary_forcing_entries) > 0:
            component_kwargs["boundary_forcing"] = []
        if isinstance(boundary_forcing_entries, dict):
            boundary_forcing_entries = [
                boundary_forcing_entries,
            ]
        for bf_kwargs in boundary_forcing_entries:
            component_kwargs["boundary_forcing"].append(
                ROMSBoundaryForcing(**bf_kwargs)
            )

        # Construct any ROMSSurfaceForcing instances:
        surface_forcing_entries = component_dict.get("surface_forcing", [])
        if len(surface_forcing_entries) > 0:
            component_kwargs["surface_forcing"] = []
        if isinstance(surface_forcing_entries, dict):
            surface_forcing_entries = [
                surface_forcing_entries,
            ]
        for sf_kwargs in surface_forcing_entries:
            component_kwargs["surface_forcing"].append(ROMSSurfaceForcing(**sf_kwargs))

        return cls(**component_kwargs)

    @property
    def component_type(self) -> str:
        return "ROMS"

    @property
    def input_datasets(self) -> list:
        """list all ROMSInputDataset objects associated with this ROMSComponent"""

        input_datasets: List[ROMSInputDataset] = []
        if self.model_grid is not None:
            input_datasets.append(self.model_grid)
        if self.initial_conditions is not None:
            input_datasets.append(self.initial_conditions)
        if self.tidal_forcing is not None:
            input_datasets.append(self.tidal_forcing)
        if len(self.boundary_forcing) > 0:
            input_datasets.extend(self.boundary_forcing)
        if len(self.surface_forcing) > 0:
            input_datasets.extend(self.surface_forcing)
        return input_datasets

    def to_dict(self) -> dict:
        # Docstring is inherited

        component_dict = super().to_dict()
        # additional source code
        namelists = getattr(self, "namelists")
        if namelists is not None:
            namelists_info = {}
            namelists_info["location"] = namelists.source.location
            if namelists.subdir is not None:
                namelists_info["subdir"] = namelists.subdir
            if namelists.checkout_target is not None:
                namelists_info["checkout_target"] = namelists.checkout_target
            if namelists.files is not None:
                namelists_info["files"] = namelists.files

            component_dict["namelists"] = namelists_info

        # Discretization
        component_dict["discretization"] = self.discretization.__dict__

        # InputDatasets:
        if self.model_grid is not None:
            component_dict["model_grid"] = self.model_grid.to_dict()
        if self.initial_conditions is not None:
            component_dict["initial_conditions"] = self.initial_conditions.to_dict()
        if self.tidal_forcing is not None:
            component_dict["tidal_forcing"] = self.tidal_forcing.to_dict()
        if len(self.surface_forcing) > 0:
            component_dict["surface_forcing"] = [
                sf.to_dict() for sf in self.surface_forcing
            ]
        if len(self.boundary_forcing) > 0:
            component_dict["boundary_forcing"] = [
                bf.to_dict() for bf in self.boundary_forcing
            ]

        return component_dict

    def setup(
        self,
        additional_source_code_dir: str | Path,
        namelist_dir: str | Path,
        input_datasets_target_dir: Optional[str | Path] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> None:
        """
        Set up this ROMSComponent instance locally.

        This method ensures the ROMSBaseModel is correctly configured, and
        that any additional code and input datasets corresponding to the
        chosen simulation period (defined by `start_date` and `end_date`)
        are made available in the chosen `additional_code_target_dir` and
        `input_datasets_target_dir` directories

        Parameters:
        -----------
        additional_code_target_dir (str or Path):
           The directory in which to save local copies of the files described by
           ROMSComponent.additional_code
        input_datasets_target_dir (str or Path):
           The directory in which to make locally accessible the input datasets
           described by ROMSComponent.input_datasets
        start_date (datetime.datetime):
           The date from which the ROMSComponent is expected to be run. Used to
           determine which input datasets are needed as part of this setup call.
        end_date (datetime.datetime):
           The date until which the ROMSComponent is expected to be run. Used to
           determine which input datasets are needed as part of this setup call.

        """
        # Setup BaseModel
        infostr = f"Configuring {self.__class__.__name__}"
        print(infostr + "\n" + "-" * len(infostr))
        self.base_model.handle_config_status()

        # Additional source code
        print(
            "\nFetching additional source code..."
            + "\n----------------------------------"
        )
        if self.additional_source_code is not None:
            self.additional_source_code.get(additional_source_code_dir)

        # Namelists
        print("\nFetching namelists... " + "\n----------------------")
        if self.namelists is not None:
            self.namelists.get(namelist_dir)

        # InputDatasets
        print("\nFetching input datasets..." + "\n--------------------------")
        for inp in self.input_datasets:
            # Download input dataset if its date range overlaps Case's date range
            if (
                ((inp.start_date is None) or (inp.end_date is None))
                or ((start_date is None) or (end_date is None))
                or (inp.start_date <= end_date)
                and (end_date >= start_date)
            ):
                if input_datasets_target_dir is None:
                    raise ValueError(
                        "ROMSComponent.input_datasets has entries "
                        + f" in the specified date range {start_date},{end_date}, "
                        + "but ROMSComponent.setup() did not receive "
                        + "a input_datasets_target_dir argument"
                    )

                if (isinstance(inp, ROMSInputDataset)) and (
                    inp.source.source_type == "yaml"
                ):
                    inp.get_from_yaml(
                        input_datasets_target_dir,
                        start_date=start_date,
                        end_date=end_date,
                    )
                else:
                    inp.get(input_datasets_target_dir)

    def build(self) -> None:
        """
        Compiles any code associated with this configuration of ROMS.
        Compilation occurs in the directory
        `ROMSComponent.additional_source_code.working_path
        This method sets the ROMSComponent `exe_path` attribute.

        """
        if self.additional_source_code is None:
            raise ValueError(
                "Unable to compile ROMSComponent: "
                + "\nROMSComponent.additional_source_code is None."
                + "\n Compile-time files are needed to build ROMS"
            )

        build_dir = self.additional_source_code.working_path
        if build_dir is None:
            raise ValueError(
                "Unable to compile ROMSComponent: "
                + "\nROMSComponent.additional_source_code.working_path is None."
                + "\n Call ROMSComponent.additional_source_code.get() and try again"
            )
        if (build_dir / "Compile").is_dir():
            make_clean_result = subprocess.run(
                "make compile_clean",
                cwd=build_dir,
                shell=True,
                capture_output=True,
                text=True,
            )
            if make_clean_result.returncode != 0:
                raise RuntimeError(
                    f"Error {make_clean_result.returncode} when compiling ROMS. STDERR stream: "
                    + f"\n {make_clean_result.stderr}"
                )

        print("Compiling UCLA-ROMS configuration...")
        make_roms_result = subprocess.run(
            f"make COMPILER={_CSTAR_COMPILER}",
            cwd=build_dir,
            shell=True,
            capture_output=True,
            text=True,
        )
        if make_roms_result.returncode != 0:
            raise RuntimeError(
                f"Error {make_roms_result.returncode} when compiling ROMS. STDERR stream: "
                + f"\n {make_roms_result.stderr}"
            )

        print(f"UCLA-ROMS compiled at {build_dir}")

        self.exe_path = build_dir / "roms"

    def pre_run(self) -> None:
        """
        Performs pre-processing steps associated with this ROMSComponent object.

        This method:
        1. goes through any netcdf files associated with InputDataset objects belonging
           to this ROMSComponent instance and partitions them such that there is one file per processor.
           The partitioned files are stored in a subdirectory `PARTITIONED` of
           InputDataset.working_path

        2. Replaces placeholder strings (if present) representing, e.g. input file paths
           in a template roms namelist file (typically `roms.in_TEMPLATE`) used to run the model with
           the respective paths to input datasets and any MARBL namelists (if this ROMS
           component belongs to a case for which MARBL is also a component).
           The namelist file is sought in
           `ROMSComponent.additional_code.working_path/namelists`.

        """

        # Partition input datasets and add their paths to namelist
        if self.input_datasets is not None and all(
            [isinstance(a, ROMSInputDataset) for a in self.input_datasets]
        ):
            datasets_to_partition = [d for d in self.input_datasets if d.exists_locally]
            # Preliminary checks
            if (self.additional_source_code is None) or (
                self.additional_source_code.working_path is None
            ):
                raise ValueError(
                    "Unable to prepare ROMSComponent for execution: "
                    + "\nROMSComponent.additional_code.working_path is None."
                    + "\n Call ROMSComponent.additional_code.get() and try again"
                )

            if not hasattr(self.namelists, "modified_files"):
                raise ValueError(
                    "No editable namelist found in which to set ROMS runtime parameters. "
                    + "Expected to find a file in ROMSComponent.namelists.files"
                    + " with the suffix '_TEMPLATE' on which to base the ROMS namelist."
                )
            else:
                if self.namelists is None:
                    raise ValueError(
                        "At least one namelist is required to run ROMS, but "
                        + "ROMSComponent.namelists is None"
                    )
                if self.namelists.working_path is None:
                    raise ValueError(
                        "No working path found for ROMSComponent.namelists. "
                        + "Call ROMSComponent.namelists.get() and try again"
                    )
                mod_namelist = (
                    self.namelists.working_path / self.namelists.modified_files[0]
                )

            namelist_forcing_str = ""
            if len(datasets_to_partition) > 0:
                from roms_tools.utils import partition_netcdf
            for f in datasets_to_partition:
                # fname = f.source.basename

                if not f.exists_locally:
                    raise ValueError(
                        f"working_path of InputDataset \n{f}\n\n {f.working_path}, "
                        + "refers to a non-existent file"
                        + "\n call InputDataset.get() and try again."
                    )
                # Partitioning step
                if f.working_path is None:
                    # Raise if inputdataset has no local working path
                    raise ValueError(f"InputDataset has no working path: {f}")
                elif isinstance(f.working_path, list):
                    # if single InputDataset corresponds to many files, check they're colocated
                    if not all(
                        [d.parent == f.working_path[0].parent for d in f.working_path]
                    ):
                        raise ValueError(
                            f"A single input dataset exists in multiple directories: {f.working_path}."
                        )
                    else:
                        # If they are, we want to partition them all in the same place
                        partdir = f.working_path[0].parent / "PARTITIONED"
                        id_files_to_partition = f.working_path[:]
                else:
                    id_files_to_partition = [
                        f.working_path,
                    ]
                    partdir = f.working_path.parent / "PARTITIONED"

                partdir.mkdir(parents=True, exist_ok=True)
                parted_files = []
                for idfile in id_files_to_partition:
                    print(
                        f"Partitioning {idfile} into ({self.discretization.n_procs_x},{self.discretization.n_procs_y})"
                    )
                    parted_files += partition_netcdf(
                        idfile,
                        np_xi=self.discretization.n_procs_x,
                        np_eta=self.discretization.n_procs_y,
                    )

                    # [p.rename(partdir / p.name) for p in parted_files[-1]]
                [p.rename(partdir / p.name) for p in parted_files]
                parted_files = [partdir / p.name for p in parted_files]
                f.partitioned_files = parted_files

                # Namelist modification step
                print(f"Adding {idfile} to ROMS namelist file")
                if isinstance(f, ROMSModelGrid):
                    if f.working_path is None or isinstance(f.working_path, list):
                        raise ValueError(
                            f"ROMS only accepts a single grid file, found list {f.working_path}"
                        )

                    assert isinstance(f.working_path, Path), "silence, linter"

                    namelist_grid_str = f"     {partdir / f.working_path.name} \n"
                    _replace_text_in_file(
                        mod_namelist, "__GRID_FILE_PLACEHOLDER__", namelist_grid_str
                    )
                elif isinstance(f, ROMSInitialConditions):
                    if f.working_path is None or isinstance(f.working_path, list):
                        raise ValueError(
                            f"ROMS only accepts a single initial conditions file, found list {f.working_path}"
                        )
                    assert isinstance(f.working_path, Path), "silence, linter"
                    namelist_ic_str = f"     {partdir / f.working_path.name} \n"
                    _replace_text_in_file(
                        mod_namelist,
                        "__INITIAL_CONDITION_FILE_PLACEHOLDER__",
                        namelist_ic_str,
                    )
                elif type(f) in [
                    ROMSSurfaceForcing,
                    ROMSTidalForcing,
                    ROMSBoundaryForcing,
                ]:
                    if isinstance(f.working_path, Path):
                        dslist = [
                            f.working_path,
                        ]
                    elif isinstance(f.working_path, list):
                        dslist = f.working_path
                    for d in dslist:
                        namelist_forcing_str += f"     {partdir / d.name} \n"

            _replace_text_in_file(
                mod_namelist, "__FORCING_FILES_PLACEHOLDER__", namelist_forcing_str
            )

            marbl_settings_path = self.namelists.working_path / "marbl_in"
            marbl_placeholder_in_namelist = _replace_text_in_file(
                mod_namelist,
                "__MARBL_SETTINGS_FILE_PLACEHOLDER__",
                str(marbl_settings_path),
            )
            if (marbl_placeholder_in_namelist) and (not marbl_settings_path.exists()):
                raise FileNotFoundError(
                    "Placeholder string for marbl_in file "
                    + "found in ROMS namelist, but 'marbl_in' not found "
                    + f"at {marbl_settings_path.parent}."
                )

            marbl_tracer_list_path = (
                self.namelists.working_path / "marbl_tracer_output_list"
            )
            marbl_placeholder_in_namelist = _replace_text_in_file(
                mod_namelist,
                "__MARBL_TRACER_LIST_FILE_PLACEHOLDER__",
                str(self.namelists.working_path / "marbl_tracer_output_list"),
            )
            if (marbl_placeholder_in_namelist) and (
                not marbl_tracer_list_path.exists()
            ):
                raise FileNotFoundError(f"{marbl_tracer_list_path} not found.")

            marbl_diag_list_path = (
                self.namelists.working_path / "marbl_diagnostics_output_list"
            )
            if (marbl_placeholder_in_namelist) and (
                not marbl_tracer_list_path.exists()
            ):
                raise FileNotFoundError(f"{marbl_diag_list_path} not found.")
            marbl_placeholder_in_namelist = _replace_text_in_file(
                mod_namelist,
                "__MARBL_DIAG_LIST_FILE_PLACEHOLDER__",
                str(self.namelists.working_path / "marbl_diagnostics_output_list"),
            )

    def run(
        self,
        n_time_steps: Optional[int] = None,
        account_key: Optional[str] = None,
        output_dir: Optional[str | Path] = None,
        walltime: Optional[str] = _CSTAR_SYSTEM_MAX_WALLTIME,
        job_name: str = "my_roms_run",
    ) -> None:
        """
        Runs the executable created by `build()`

        This method creates a temporary file to be submitted to the job scheduler (if any)
        on the calling machine, then submits it. By default the job requests the maximum
        walltime. It calculates the number of nodes and cores-per-node to request based on
        the number of cores required by the job, `ROMSComponent.discretization.n_procs_tot`.

        Parameters:
        -----------
        account_key: str, default None
            The users account key on the system
        output_dir: str or Path:
            The path to the directory in which model output will be saved. This is by default
            the directory from which the ROMS executable will be called.
        walltime: str, default _CSTAR_SYSTEM_MAX_WALLTIME
            The requested length of the job, HH:MM:SS
        job_name: str, default 'my_roms_run'
            The name of the job submitted to the scheduler, which also sets the output file name
            `job_name.out`
        """

        if self.exe_path is None:
            raise ValueError(
                "C-STAR: ROMSComponent.exe_path is None; unable to find ROMS executable."
                + "\nRun Component.build() first. "
                + "\n If you have already run Component.build(), either run it again or "
                + " add the executable path manually using Component.exe_path='YOUR/PATH'."
            )
        if output_dir is None:
            output_dir = self.exe_path.parent
        output_dir = Path(output_dir)

        # Set run path to output dir for clarity: we are running in the output dir but
        # these are conceptually different:
        run_path = output_dir

        if self.namelists is None:
            raise FileNotFoundError(
                "C-STAR: Unable to find namelist file (typically roms.in) "
                + "associated with this ROMSComponent."
            )
            return

        # Add number of timesteps to namelist
        # Check if n_time_steps is None, indicating it was not explicitly set
        if n_time_steps is None:
            n_time_steps = 1
            warnings.warn(
                "n_time_steps not explicitly set, using default value of 1. "
                "Please call ROMSComponent.run() with the n_time_steps argument "
                "to specify the length of the run.",
                UserWarning,
            )
        assert isinstance(n_time_steps, int)

        if hasattr(self.namelists, "modified_files"):
            mod_namelist = (
                self.namelists.working_path / self.namelists.modified_files[0]
            )
            _replace_text_in_file(
                mod_namelist,
                "__NTIMES_PLACEHOLDER__",
                str(n_time_steps),
            )
            _replace_text_in_file(
                mod_namelist,
                "__TIMESTEP_PLACEHOLDER__",
                str(self.discretization.time_step),
            )

        else:
            raise ValueError(
                "No editable namelist found to set ROMS runtime parameters. "
                + "Expected to find a file in ROMSComponent.namelists"
                + " with the suffix '_TEMPLATE' on which to base the ROMS namelist."
            )
        output_dir.mkdir(parents=True, exist_ok=True)

        match _CSTAR_SYSTEM:
            case "sdsc_expanse":
                exec_pfx = "srun --mpi=pmi2"
            case "nersc_perlmutter":
                exec_pfx = "srun"
            case "ncar_derecho":
                exec_pfx = "mpirun"
            case "osx_arm64":
                exec_pfx = "mpirun"
            case "linux_x86_64":
                exec_pfx = "mpirun"

        roms_exec_cmd = (
            f"{exec_pfx} -n {self.discretization.n_procs_tot} {self.exe_path} "
            + f"{mod_namelist}"
        )

        if self.discretization.n_procs_tot is not None:
            if _CSTAR_SYSTEM_CORES_PER_NODE is not None:
                nnodes, ncores = _calculate_node_distribution(
                    self.discretization.n_procs_tot, _CSTAR_SYSTEM_CORES_PER_NODE
                )
            else:
                raise ValueError(
                    f"Unable to calculate node distribution for system: {_CSTAR_SYSTEM}."
                    + "\nC-Star is unaware of your system's node configuration (cores per node)."
                    + "\nYour system may be unsupported. Please raise an issue at: "
                    + "\n https://github.com/CWorthy-ocean/C-Star/issues/new"
                    + "\n Thank you in advance for your contribution!"
                )
        else:
            raise ValueError(
                "Unable to calculate node distribution for this Component. "
                + "Component.n_procs_tot is not set"
            )
        match _CSTAR_SCHEDULER:
            case "pbs":
                if account_key is None:
                    raise ValueError(
                        "please call Component.run() with a value for account_key"
                    )
                scheduler_script = "#PBS -S /bin/bash"
                scheduler_script += f"\n#PBS -N {job_name}"
                scheduler_script += f"\n#PBS -o {job_name}.out"
                scheduler_script += f"\n#PBS -A {account_key}"
                scheduler_script += (
                    f"\n#PBS -l select={nnodes}:ncpus={ncores},walltime={walltime}"
                )
                scheduler_script += f"\n#PBS -q {_CSTAR_SYSTEM_DEFAULT_PARTITION}"
                scheduler_script += "\n#PBS -j oe"
                scheduler_script += "\n#PBS -k eod"
                scheduler_script += "\n#PBS -V"
                if _CSTAR_SYSTEM == "ncar_derecho":
                    scheduler_script += "\ncd ${PBS_O_WORKDIR}"
                scheduler_script += f"\n\n{roms_exec_cmd}"

                script_fname = "cstar_run_script.pbs"
                with open(run_path / script_fname, "w") as f:
                    f.write(scheduler_script)
                subprocess.run(f"qsub {script_fname}", shell=True, cwd=run_path)

            case "slurm":
                # TODO: export ALL copies env vars, but will need to handle module load
                if account_key is None:
                    raise ValueError(
                        "please call Component.run() with a value for account_key"
                    )

                scheduler_script = "#!/bin/bash"
                scheduler_script += f"\n#SBATCH --job-name={job_name}"
                scheduler_script += f"\n#SBATCH --output={job_name}.out"
                if _CSTAR_SYSTEM == "nersc_perlmutter":
                    scheduler_script += (
                        f"\n#SBATCH --qos={_CSTAR_SYSTEM_DEFAULT_PARTITION}"
                    )
                    scheduler_script += "\n#SBATCH -C cpu"
                else:
                    scheduler_script += (
                        f"\n#SBATCH --partition={_CSTAR_SYSTEM_DEFAULT_PARTITION}"
                    )
                    # FIXME: This ^^^ is a pretty ugly patch...
                scheduler_script += f"\n#SBATCH --nodes={nnodes}"
                scheduler_script += f"\n#SBATCH --ntasks-per-node={ncores}"
                scheduler_script += f"\n#SBATCH --account={account_key}"
                scheduler_script += "\n#SBATCH --export=ALL"
                scheduler_script += "\n#SBATCH --mail-type=ALL"
                scheduler_script += f"\n#SBATCH --time={walltime}"
                scheduler_script += f"\n\n{roms_exec_cmd}"

                script_fname = "cstar_run_script.sh"
                with open(run_path / script_fname, "w") as f:
                    f.write(scheduler_script)
                subprocess.run(f"sbatch {script_fname}", shell=True, cwd=run_path)

            case None:
                import time

                romsprocess = subprocess.Popen(
                    roms_exec_cmd,
                    shell=True,
                    cwd=run_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                # Process stdout line-by-line
                tstep0 = 0
                roms_init_string = ""
                T0 = time.time()
                if romsprocess.stdout is None:
                    raise RuntimeError("ROMS is not producing stdout")

                # 2024-09-21 : the following is included as in some instances ROMS
                # will exit with code 0 even if a fatal error occurs, see:
                # https://github.com/CESR-lab/ucla-roms/issues/42

                debugging = False  # Print raw output if true
                if debugging:
                    while True:
                        output = romsprocess.stdout.readline()
                        if output == "" and romsprocess.poll() is not None:
                            break
                        if output:
                            print(output.strip())
                else:
                    for line in romsprocess.stdout:
                        # Split the line by whitespace
                        parts = line.split()

                        # Check if there are exactly 9 parts and the first one is an integer
                        if len(parts) == 9:
                            try:
                                # Try to convert the first part to an integer
                                tstep = int(parts[0])
                                if tstep0 == 0:
                                    tstep0 = tstep
                                    # Capture the first integer and print it
                                ETA = (n_time_steps - (tstep - tstep0)) * (
                                    (tstep - tstep0) / (time.time() - T0)
                                )
                                print(
                                    f"Running ROMS: time-step {tstep-tstep0} of {n_time_steps} ({time.time()-T0:.1f}s elapsed; ETA {ETA:.1f}s)"
                                )
                            except ValueError:
                                pass
                        elif tstep0 == 0 and len(roms_init_string) == 0:
                            roms_init_string = "Running ROMS: Initializing run..."
                            print(roms_init_string)

                romsprocess.wait()
                if romsprocess.returncode != 0:
                    import datetime as dt

                    errlog = (
                        output_dir
                        / f"ROMS_STDERR_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                    )
                    if romsprocess.stderr is not None:
                        with open(errlog, "w") as F:
                            F.write(romsprocess.stderr.read())
                    raise RuntimeError(
                        f"ROMS terminated with errors. See {errlog} for further information."
                    )

    def post_run(self, output_dir=None) -> None:
        """
        Performs post-processing steps associated with this ROMSComponent object.

        This method goes through any netcdf files produced by the model in
        `output_dir` and joins netcdf files that are produced separately by each processor.

        Parameters:
        -----------
        output_dir: str | Path
            The directory in which output was produced by the run
        """
        output_dir = Path(output_dir)
        files = list(output_dir.glob("*.*0.nc"))
        if not files:
            print("no suitable output found")
        else:
            (output_dir / "PARTITIONED").mkdir(exist_ok=True)
            for f in files:
                # Want to go from, e.g. myfile.001.nc to myfile.*.nc, so we apply stem twice:
                wildcard_pattern = f"{Path(f.stem).stem}.*.nc"
                print(f"Joining netCDF files {wildcard_pattern}...")
                ncjoin_result = subprocess.run(
                    f"ncjoin {wildcard_pattern}",
                    cwd=output_dir,
                    capture_output=True,
                    text=True,
                    shell=True,
                )
                if ncjoin_result.returncode != 0:
                    raise RuntimeError(
                        f"Error {ncjoin_result.returncode} while joining ROMS output. "
                        + f"STDERR stream:\n {ncjoin_result.stderr}"
                    )
                for F in output_dir.glob(wildcard_pattern):
                    F.rename(output_dir / "PARTITIONED" / F.name)


class ROMSDiscretization(Discretization):
    """
    An implementation of the Discretization class for ROMS.


    Additional attributes:
    ----------------------
    n_procs_x: int
        The number of parallel processors over which to subdivide the x axis of the domain.
    n_procs_y: int
        The number of parallel processors over which to subdivide the y axis of the domain.

    Properties:
    -----------
    n_procs_tot: int
        The value of n_procs_x * n_procs_y

    """

    def __init__(
        self,
        time_step: int,
        n_procs_x: int = 1,
        n_procs_y: int = 1,
    ):
        """
        Initialize a ROMSDiscretization object from basic discretization parameters

        Parameters:
        -----------
        time_step: int
            The time step with which to run the Component
        n_procs_x: int
           The number of parallel processors over which to subdivide the x axis of the domain.
        n_procs_y: int
           The number of parallel processors over which to subdivide the y axis of the domain.


        Returns:
        --------
        ROMSDiscretization:
            An initialized ROMSDiscretization object

        """

        super().__init__(time_step)
        self.n_procs_x = n_procs_x
        self.n_procs_y = n_procs_y

    @property
    def n_procs_tot(self) -> int:
        """Total number of processors required by this ROMS configuration"""
        return self.n_procs_x * self.n_procs_y

    def __str__(self) -> str:
        disc_str = super().__str__()

        if hasattr(self, "n_procs_x") and self.n_procs_x is not None:
            disc_str += (
                "\nn_procs_x: "
                + str(self.n_procs_x)
                + " (Number of x-direction processors)"
            )
        if hasattr(self, "n_procs_y") and self.n_procs_y is not None:
            disc_str += (
                "\nn_procs_y: "
                + str(self.n_procs_y)
                + " (Number of y-direction processors)"
            )
        return disc_str

    def __repr__(self) -> str:
        repr_str = super().__repr__().strip(")")
        if hasattr(self, "n_procs_x") and self.n_procs_x is not None:
            repr_str += f", n_procs_x = {self.n_procs_x}, "
        if hasattr(self, "n_procs_y") and self.n_procs_y is not None:
            repr_str += f"n_procs_y = {self.n_procs_y}, "

        repr_str = repr_str.strip(", ")
        repr_str += ")"

        return repr_str
