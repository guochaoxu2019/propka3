"""Molecular container for storing all contents of PDB files."""
import os
import sys
import propka.version
from propka.pdb import read_input
from propka.output import write_input
from propka.conformation_container import ConformationContainer
from propka.lib import info, warning, make_grid


# TODO - these are constants whose origins are a little murky
UNK_PI_CUTOFF = 0.01
# Maximum number of iterations for finding PI
MAX_ITERATION = 4


class Molecular_container:
    """Container for storing molecular contents of PDB files.

    TODO - this class name does not conform to PEP8 but has external use.
    We should deprecate and change eventually.
    """

    def __init__(self, input_file, options=None):
        """Initialize molecular container.

        Args:
            input_file:  molecular input file
            options:  options object
        """
        # printing out header before parsing input
        propka.output.print_header()
        # set up some values
        self.options = options
        self.input_file = input_file
        # TODO - replace this indelicate os.path code with pathlib
        self.dir = os.path.split(input_file)[0]
        self.file = os.path.split(input_file)[1]
        self.name = self.file[0:self.file.rfind('.')]
        input_file_extension = input_file[input_file.rfind('.'):]
        # set the version
        if options:
            parameters = propka.parameters.Parameters(self.options.parameters)
        else:
            parameters = propka.parameters.Parameters('propka.cfg')
        try:
            version_class = getattr(propka.version, parameters.version)
            self.version = version_class(parameters)
        except AttributeError as err:
            print(err)
            errstr = 'Error: Version {0:s} does not exist'.format(
                parameters.version)
            raise Exception(errstr)
        # read the input file
        if input_file_extension[0:4] == '.pdb':
            # input is a pdb file. read in atoms and top up containers to make
            # sure that all atoms are present in all conformations
            [self.conformations, self.conformation_names] = (
                propka.pdb.read_pdb(input_file, self.version.parameters, self))
            if len(self.conformations) == 0:
                info('Error: The pdb file does not seems to contain any '
                     'molecular conformations')
                sys.exit(-1)
            self.top_up_conformations()
            # make a structure precheck
            propka.pdb.protein_precheck(self.conformations,
                                        self.conformation_names)
            # set up atom bonding and protonation
            self.version.setup_bonding_and_protonation(self)
            # Extract groups
            self.extract_groups()
            # sort atoms
            for name in self.conformation_names:
                self.conformations[name].sort_atoms()
            # find coupled groups
            self.find_covalently_coupled_groups()
            # write out the input file
            filename = self.file.replace(input_file_extension, '.propka_input')
            write_input(self, filename)
        elif input_file_extension == '.propka_input':
            #input is a propka_input file
            [self.conformations, self.conformation_names] = (
                propka.pdb.read_input(input_file, self.version.parameters,
                                      self))
            # Extract groups - this merely sets up the groups found in the
            # input file
            self.extract_groups()
            # do some additional set up
            self.additional_setup_when_reading_input_file()
        else:
            info('Unrecognized input file:{0:s}'.format(input_file))
            sys.exit(-1)

    def top_up_conformations(self):
        """Makes sure that all atoms are present in all conformations."""
        for name in self.conformation_names:
            if (name != '1A' and (len(self.conformations[name])
                                  < len(self.conformations['1A']))):
                self.conformations[name].top_up(self.conformations['1A'])

    def find_covalently_coupled_groups(self):
        """Find covalently coupled groups."""
        info('-' * 103)
        for name in self.conformation_names:
            self.conformations[name].find_covalently_coupled_groups()

    def find_non_covalently_coupled_groups(self):
        """Find non-covalently coupled groups."""
        info('-' * 103)
        verbose = self.options.display_coupled_residues
        for name in self.conformation_names:
            self.conformations[name].find_non_covalently_coupled_groups(
                verbose=verbose)

    def extract_groups(self):
        """Identify the groups needed for pKa calculation."""
        for name in self.conformation_names:
            self.conformations[name].extract_groups()

    def additional_setup_when_reading_input_file(self):
        """Additional setup."""
        for name in self.conformation_names:
            self.conformations[name].additional_setup_when_reading_input_file()

    def calculate_pka(self):
        """Calculate pKa values."""
        # calculate for each conformation
        for name in self.conformation_names:
            self.conformations[name].calculate_pka(
                self.version, self.options)
        # find non-covalently coupled groups
        self.find_non_covalently_coupled_groups()
        # find the average of the conformations
        self.average_of_conformations()
        # print out the conformation-average results
        propka.output.print_result(self, 'AVR', self.version.parameters)

    def average_of_conformations(self):
        """Generate an average of conformations."""
        parameters = self.conformations[self.conformation_names[0]].parameters
        # make a new configuration to hold the average values
        avr_conformation = ConformationContainer(
            name='average', parameters=parameters, molecular_container=self)
        container = self.conformations[self.conformation_names[0]]
        for group in container.get_groups_for_calculations():
            # new group to hold average values
            avr_group = group.clone()
            # sum up all groups ...
            for name in self.conformation_names:
                group_to_add = self.conformations[name].find_group(group)
                if group_to_add:
                    avr_group += group_to_add
                else:
                    str_ = (
                        'Group {0:s} could not be found in '
                        'conformation {1:s}.'.format(
                            group.atom.residue_label, name))
                    warning(str_)
            # ... and store the average value
            avr_group = avr_group / len(self.conformation_names)
            avr_conformation.groups.append(avr_group)
        # store information on coupling in the average container
        if len(list(filter(lambda c: c.non_covalently_coupled_groups,
                           self.conformations.values()))):
            avr_conformation.non_covalently_coupled_groups = True
        # store chain info
        avr_conformation.chains = self.conformations[
            self.conformation_names[0]].chains
        self.conformations['AVR'] = avr_conformation

    def write_pka(self, filename=None, reference="neutral",
                  direction="folding", options=None):
        """Write pKa information to a file.

        Args:
            filename:  file to write to
            reference:  reference state
            direction:  folding vs. unfolding
            options:  options object
        """
        # write out the average conformation
        filename = os.path.join('{0:s}.pka'.format(self.name))
        # if the display_coupled_residues option is true, write the results out
        # to an alternative pka file
        if self.options.display_coupled_residues:
            filename = os.path.join('{0:s}_alt_state.pka'.format(self.name))
        if (hasattr(self.version.parameters, 'output_file_tag')
                and len(self.version.parameters.output_file_tag) > 0):
            filename = os.path.join(
                '{0:s}_{1:s}.pka'.format(
                    self.name, self.version.parameters.output_file_tag))
        propka.output.write_pka(
            self, self.version.parameters, filename=filename,
            conformation='AVR', reference=reference)

    def get_folding_profile(self, conformation='AVR', reference="neutral",
                            grid=[0., 14., 0.1]):
        """Get a folding profile.

        Args:
            conformation:  conformation to select
            reference:  reference state
            direction:  folding direction (folding)
            grid:  the grid of pH values [min, max, step_size]
            options:  options object
        Returns:
            TODO - figure out what these are
            1. profile
            2. opt
            3. range_80pct
            4. stability_range
        """
        # calculate stability profile
        profile = []
        for ph in make_grid(*grid):
            conf = self.conformations[conformation]
            ddg = conf.calculate_folding_energy(ph=ph, reference=reference)
            profile.append([ph, ddg])
        # find optimum
        opt = [None, 1e6]
        for point in profile:
            opt = min(opt, point, key=lambda v: v[1])
        # find values within 80 % of optimum
        range_80pct = [None, None]
        values_within_80pct = [p[0] for p in profile if p[1] < 0.8*opt[1]]
        if len(values_within_80pct) > 0:
            range_80pct = [min(values_within_80pct), max(values_within_80pct)]
        # find stability range
        stability_range = [None, None]
        stable_values = [p[0] for p in profile if p[1] < 0.0]
        if len(stable_values) > 0:
            stability_range = [min(stable_values), max(stable_values)]
        return profile, opt, range_80pct, stability_range

    def get_charge_profile(self, conformation='AVR', grid=[0., 14., .1]):
        """Get charge profile for conformation as function of pH.

        Args:
            conformation:  conformation to test
            grid:  grid of pH values [min, max, step]
        Returns:
            list of charge state values
        """
        charge_profile = []
        for ph in make_grid(*grid):
            conf = self.conformations[conformation]
            q_unfolded, q_folded = conf.calculate_charge(
                self.version.parameters, ph=ph)
            charge_profile.append([ph, q_unfolded, q_folded])
        return charge_profile

    def get_pi(self, conformation='AVR', grid=[0., 14., 1], iteration=0):
        """Get the isoelectric points for folded and unfolded states.

        Args:
            conformation:  conformation to test
            grid:  grid of pH values [min, max, step]
            iteration:  iteration number of process
        Returns:
            1. Folded state PI
            2. Unfolded state PI
        """
        charge_profile = self.get_charge_profile(
            conformation=conformation, grid=grid)
        pi_folded = pi_unfolded = [None, 1e6, 1e6]
        for point in charge_profile:
            pi_folded = min(pi_folded, point, key=lambda v: abs(v[2]))
            pi_unfolded = min(pi_unfolded, point, key=lambda v: abs(v[1]))
        # If results are not good enough, do it again with a higher sampling
        # resolution
        pi_folded_value = pi_folded[0]
        pi_unfolded_value = pi_unfolded[0]
        step = grid[2]
        # TODO - need to warn if maximum number of iterations is exceeded
        if ((pi_folded[2] > UNK_PI_CUTOFF
             or pi_unfolded[1] > UNK_PI_CUTOFF) and iteration < MAX_ITERATION):
            pi_folded_value, _ = self.get_pi(
                conformation=conformation,
                grid=[pi_folded[0]-step, pi_folded[0]+step, step/10.0],
                iteration=iteration+1)
            _, pi_unfolded_value = self.get_pi(
                conformation=conformation,
                grid=[pi_unfolded[0]-step, pi_unfolded[0]+step, step/10.0],
                iteration=iteration+1)
        return pi_folded_value, pi_unfolded_value
