# Copyright 2020 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Performance benchmark for CPU emulation.

Test cases are defined in /configs directory. To add a new test
case just create a new config file with the desired test scenario.
Job examples for each supported feature(CPU, Cache, VM etc) can be
found in /usr/share/stress-ng/example-jobs.All the configs in
/config will be ran by run_test().
"""

import os
from subprocess import PIPE, run
import yaml

import host_tools.network as net_tools

REMOTE_PATH = '/tmp'

CONFIGS_DIR = 'configs'

# Success message used to check if a guest test was successful
SUCCESS_MSG = 'successful run completed'

# Target performance
TARGET = 0.95

TARGET_METRIC = 'bogo-ops-per-second-real-time'

STRESS_NG_CMD = 'taskset -c {} stress-ng --job {} --yaml {}'

PINNED_HOST_CPU = 71

PINNED_GUEST_CPU = 3

OUTPUT_FILE = 'out.yaml'

RETRIEVE_RESULTS_CMD = 'cat {out} && rm {out}'.format(out=OUTPUT_FILE)


def test_cpu_performance(test_microvm_with_ssh, network_config):
    """Execute CPU emulation tests."""
    microvm = test_microvm_with_ssh
    microvm.spawn()
    microvm.basic_config(mem_size_mib=1024, vcpu_count=4)

    _tap, _, _ = microvm.ssh_network_config(network_config, '1')

    microvm.start()
    ssh_connection = net_tools.SSHConnection(microvm.ssh_config)

    # Path to the configuration files
    file_name = os.path.basename(__file__)
    # This variable has the following format : path/to/test::test_name [image]
    # Drop the second part and replace the file name with the config directory
    local_config_dir = os.getenv('PYTEST_CURRENT_TEST') \
        .split('::')[0] \
        .replace(file_name, CONFIGS_DIR)

    # Copy config file on guest
    ssh_connection.scp_file(local_config_dir, REMOTE_PATH, recursive=True)
    remote_config_dir = os.path.join(REMOTE_PATH, CONFIGS_DIR)

    results = dict()
    for test_case in os.listdir(local_config_dir):
        local_job_path = os.path.join(local_config_dir, test_case)
        remote_job_path = os.path.join(remote_config_dir, test_case)

        # Run the test and store the result
        results[test_case] = run_test(ssh_connection, remote_job_path,
                                      local_job_path)

    # Once the tests are done, remove the configs on the guest
    _, _, _ = ssh_connection.execute_command('rm -rf {}'.
                                             format(remote_config_dir))

    for key, value in results.items():
        performance = value[1] / value[0]
        passed = 'PASS' if performance >= TARGET else 'FAIL'
        print('{} {} - {} host : {} guest : {}'.format(key, performance, passed, value[0], value[1]))
        #assert performance >= TARGET

    assert 1 == 0


def run_local_command(cmd):
    """Run a command on host."""
    proc = run(cmd, stdout=PIPE, stderr=PIPE, shell=True,
               universal_newlines=True)
    return proc.stdout, proc.stderr


def get_target_metric(result):
    """Get the target metric from a yaml output."""
    return result['metrics'][0][TARGET_METRIC]


def run_test(ssh_connection, remote_job_path, local_job_path):
    """Run the test case.

    Tests are ran in 2 steps as follows:
    - firstly, we run stress-ng and check if the stderr is clean.
    - secondly, we read the out.yaml file which contains the
    results of the test.
    We are doing this since the stdout of stress-ng is going to
    stderr too so if an error occurs we won't be able to detect
    it just by checking if stderr is empty. Splitting the process
    this way allows us to detect eventual errors occurred
    during first step.
    """
    # Run command on host
    stress_ng_cmd = STRESS_NG_CMD.format(PINNED_HOST_CPU, local_job_path, OUTPUT_FILE)
    _, host_stderr = run_local_command(stress_ng_cmd)

    assert SUCCESS_MSG in host_stderr

    host_stdout, host_stderr = run_local_command(RETRIEVE_RESULTS_CMD)

    assert host_stderr == ''
    host_result = yaml.safe_load(host_stdout)

    # Run command on guest
    stress_ng_cmd = STRESS_NG_CMD.format(PINNED_GUEST_CPU, remote_job_path, OUTPUT_FILE)
    _, _, guest_stderr = ssh_connection.execute_command(stress_ng_cmd)

    assert SUCCESS_MSG in guest_stderr.read().decode('utf-8')

    _, stdout, stderr = ssh_connection.execute_command(RETRIEVE_RESULTS_CMD)

    assert stderr.read().decode('utf-8') == ''
    guest_result = yaml.safe_load(stdout.read().decode('utf-8'))

    return get_target_metric(host_result), get_target_metric(guest_result)
