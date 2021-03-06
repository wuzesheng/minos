#!/usr/bin/env python

import deploy_utils
import os
import pwd
import subprocess
import sys
import tempfile
import time

from deploy_utils import Log

MYID_FILE = "myid"

SHELL_COMMAND_INFO = {
  "zkcli": ("org.apache.zookeeper.ZooKeeperMain",
      "run the zookeeper client shell"),
}

def generate_zookeeper_config(args):
  config_dict = args.zookeeper_config.configuration.generated_files["zookeeper.cfg"]
  local_path = "%s/zookeeper.cfg.tmpl" % deploy_utils.get_template_dir()
  template = deploy_utils.Template(open(local_path, "r").read())
  return template.substitute(config_dict)

def generate_jaas_config(args):
  if not deploy_utils.is_security_enabled(args):
    return ""

  config_dict = args.zookeeper_config.configuration.generated_files["jaas-server.conf"]

  for key, value in config_dict.items()[1:]:
    if value != "true" and value != "false" and value.find("\"") == -1:
      config_dict[key] = "\"" + value + "\""

  header_line = config_dict["headerLine"]
  return "Server {\n  %s\n%s;\n};" % (header_line,
      "\n".join(["  %s=%s" % (key, value)
        for (key, value) in config_dict.iteritems() if key != config_dict.keys()[0]]))

def generate_client_jaas_config(args):
  if not deploy_utils.is_security_enabled(args):
    return ""

  config_dict = args.zookeeper_config.configuration.generated_files["jaas-client.conf"]

  for key, value in config_dict.items()[1:]:
    if value != "true" and value != "false" and value.find("\"") == -1:
      config_dict[key] = "\"" + value + "\""

  header_line = config_dict["headerLine"]
  return "Client {\n  %s\n%s;\n};" % (header_line,
      "\n".join(["  %s=%s" % (key, value)
        for (key, value) in config_dict.iteritems() if key != config_dict.keys()[0]]))

def generate_run_scripts(args):
  config_files = dict()

  zookeeper_cfg = generate_zookeeper_config(args)
  jaas_conf = generate_jaas_config(args)
  log4j_xml = args.zookeeper_config.configuration.raw_files["log4j.xml"]
  krb5_conf = args.zookeeper_config.configuration.raw_files["krb5.conf"]

  config_files.update({
      "zookeeper.cfg": zookeeper_cfg,
      "jaas.conf": jaas_conf,
      "log4j.xml": log4j_xml,
      "krb5.conf": krb5_conf,
  })
  return config_files

def generate_bootstrap_script(args, host, job_name, host_id):
  supervisor_client = deploy_utils.get_supervisor_client(host,
    "zookeeper", args.zookeeper_config.cluster.name, job_name)
  data_dir = supervisor_client.get_available_data_dirs()[0]
  myid_file = "%s/%s" % (data_dir, MYID_FILE)

  script_dict = {
    'myid_file': myid_file,
    'host_id': host_id,
  }
  return deploy_utils.create_run_script(
      '%s/bootstrap_zk.sh.tmpl' % deploy_utils.get_template_dir(),
      script_dict)

def generate_start_script(args, host, job_name):
  supervisor_client = deploy_utils.get_supervisor_client(host,
      "zookeeper", args.zookeeper_config.cluster.name, job_name)
  run_dir = supervisor_client.get_run_dir()

  artifact_and_version = "zookeeper-" + args.zookeeper_config.cluster.version
  component_dir = "$package_dir"
  # must include both [dir]/ and [dir]/* as [dir]/* only import all jars under
  # this dir but we also need access the webapps under this dir.
  jar_dirs = "%s/:%s/lib/*:%s/*" % (component_dir, component_dir, component_dir)
  job = args.zookeeper_config.jobs["zookeeper"]

  script_dict = {
      "artifact": artifact_and_version,
      "job_name": job_name,
      "jar_dirs": jar_dirs,
      "run_dir": run_dir,
      "params":
          '-Xmx%dm ' % job.xmx +
          '-Xms%dm ' % job.xms +
          '-Xmn%dm ' % job.xmn +
          '-XX:MaxDirectMemorySize=%dm ' % job.max_direct_memory +
          '-XX:MaxPermSize=%dm ' % job.max_perm_size +
          '-XX:+DisableExplicitGC ' +
          '-XX:+HeapDumpOnOutOfMemoryError ' +
          '-XX:HeapDumpPath=$log_dir ' +
          '-XX:+PrintGCApplicationStoppedTime ' +
          '-XX:+UseConcMarkSweepGC ' +
          '-XX:CMSInitiatingOccupancyFraction=80 ' +
          '-XX:+UseMembar ' +
          '-verbose:gc ' +
          '-XX:+PrintGCDetails ' +
          '-XX:+PrintGCDateStamps ' +
          '-Xloggc:$run_dir/stdout/zk_gc_${start_time}.log ' +
          '-Djava.net.preferIPv4Stack=true ' +
          '-Dzookeeper.log.dir=$log_dir ' +
          '-Dzookeeper.cluster=%s ' % args.zookeeper_config.cluster.name +
          '-Dzookeeper.tracelog.dir=$log_dir ',
  }

  # Config security
  if deploy_utils.is_security_enabled(args):
    script_dict["params"] += '-Dzookeeper.superUser=zk_admin '
    script_dict["params"] += '-Djava.security.auth.login.config=$run_dir/jaas.conf '
    script_dict["params"] += '-Djava.security.krb5.conf=$run_dir/krb5.conf '

  script_dict["params"] += 'org.apache.zookeeper.server.quorum.QuorumPeerMain '
  script_dict["params"] += '$run_dir/zookeeper.cfg'

  return deploy_utils.create_run_script(
      '%s/start.sh.tmpl' % deploy_utils.get_template_dir(),
      script_dict)

def get_zk_service_config(args):
  args.zookeeper_config = deploy_utils.get_service_config(args)
  if args.zookeeper_config.cluster.zk_cluster:
    Log.print_critical(
        "zookeeper cluster can't depends on other clusters: %s" %
        args.zookeeper_config.cluster.name)

def install(args):
  get_zk_service_config(args)
  deploy_utils.install_service(args, "zookeeper", args.zookeeper_config, "zookeeper")

def cleanup(args):
  get_zk_service_config(args)

  cleanup_token = deploy_utils.confirm_cleanup(args,
      "zookeeper", args.zookeeper_config)

  hosts = args.zookeeper_config.jobs["zookeeper"].hosts
  for id, host in hosts.iteritems():
    deploy_utils.cleanup_job("zookeeper", args.zookeeper_config,
        hosts[id], "zookeeper", cleanup_token)

def bootstrap_job(args, host, job_name, host_id, cleanup_token):
  bootstrap_script = generate_bootstrap_script(args, host, job_name, host_id)
  deploy_utils.bootstrap_job(args, "zookeeper", "zookeeper", args.zookeeper_config,
      host, job_name, cleanup_token, '0', bootstrap_script)

  # start job after bootstrapping.
  start_job(args, host, job_name)

def bootstrap(args):
  get_zk_service_config(args)

  cleanup_token = deploy_utils.confirm_bootstrap("zookeeper", args.zookeeper_config)

  hosts = args.zookeeper_config.jobs["zookeeper"].hosts
  for id in args.task or hosts.iterkeys():
    bootstrap_job(args, hosts[id], "zookeeper", id, cleanup_token)

def start_job(args, host, job_name):
  config_files = generate_run_scripts(args)
  start_script = generate_start_script(args, host, job_name)
  http_url = ''
  deploy_utils.start_job(args, "zookeeper", "zookeeper", args.zookeeper_config,
      host, job_name, start_script, http_url, **config_files)

def start(args):
  if not args.skip_confirm:
    deploy_utils.confirm_start(args)
  get_zk_service_config(args)

  hosts = args.zookeeper_config.jobs["zookeeper"].hosts
  if args.host is not None:
    args.task = deploy_utils.get_task_by_hostname(hosts, args.host)
  for id in args.task or hosts.iterkeys():
    start_job(args, hosts[id], "zookeeper")

def stop_job(args, host, job_name):
  deploy_utils.stop_job("zookeeper", args.zookeeper_config,
      host, job_name)

def stop(args):
  if not args.skip_confirm:
    deploy_utils.confirm_stop(args)
  get_zk_service_config(args)

  hosts = args.zookeeper_config.jobs["zookeeper"].hosts
  if args.host is not None:
    args.task = deploy_utils.get_task_by_hostname(hosts, args.host)
  for id in args.task or hosts.iterkeys():
    stop_job(args, hosts[id], "zookeeper")

def restart(args):
  if not args.skip_confirm:
    deploy_utils.confirm_restart(args)
  get_zk_service_config(args)

  hosts = args.zookeeper_config.jobs["zookeeper"].hosts
  if args.host is not None:
    args.task = deploy_utils.get_task_by_hostname(hosts, args.host)
  for id in args.task or hosts.iterkeys():
    stop_job(args, hosts[id], "zookeeper")

  for id in args.task or hosts.iterkeys():
    deploy_utils.wait_for_job_stopping("zookeeper",
        args.zookeeper_config.cluster.name, "zookeeper", hosts[id])
    start_job(args, hosts[id], "zookeeper")

def show(args):
  get_zk_service_config(args)

  hosts = args.zookeeper_config.jobs["zookeeper"].hosts
  if args.host is not None:
    args.task = deploy_utils.get_task_by_hostname(hosts, args.host)
  for id in args.task or hosts.iterkeys():
    deploy_utils.show_job("zookeeper", args.zookeeper_config,
        hosts[id], "zookeeper")

def run_shell(args):
  get_zk_service_config(args)

  main_class, options = deploy_utils.parse_shell_command(
      args, SHELL_COMMAND_INFO)
  if not main_class:
    return

  client_jaas = generate_client_jaas_config(args)
  jaas_fd, jaas_file = tempfile.mkstemp(suffix='zookeeper')
  os.write(jaas_fd, client_jaas)
  os.close(jaas_fd)
  zookeeper_opts = list()
  if deploy_utils.is_security_enabled(args):
    zookeeper_opts.append("-Djava.security.auth.login.config=%s" % jaas_file)
    zookeeper_opts.append(
      "-Djava.security.krb5.conf=%s/krb5-hadoop.conf" %
      deploy_utils.get_config_dir())

  package_root = deploy_utils.get_zookeeper_package_root(
      args.zookeeper_config.cluster.version)
  class_path = "%s/:%s/lib/*:%s/*" % (package_root, package_root, package_root)

  zk_address = "%s:%d" % (
      deploy_utils.get_zk_address(args.zookeeper_config.cluster.name),
      args.zookeeper_config.jobs["zookeeper"].base_port)

  cmd = (["java", "-cp", class_path] + zookeeper_opts + [main_class,
      "-server", zk_address] + options)
  p = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
  p.wait()

def generate_client_config(args, artifact, version):
  config_path = "%s/%s/%s-%s/conf" % (args.package_root,
      args.cluster, artifact, version)
  deploy_utils.write_file("%s/zookeeper.cfg" % config_path,
      generate_zookeeper_config(args))
  deploy_utils.write_file("%s/jaas.conf" % config_path,
      generate_client_jaas_config(args))
  deploy_utils.write_file("%s/krb5.conf" % config_path,
      args.zookeeper_config.configuration.raw_files["krb5.conf"])
  update_zk_env_sh(args, artifact, version)

def update_zk_env_sh(args, artifact, version):
  current_path = os.path.abspath(os.path.dirname(
        os.path.realpath(args.package_root)))
  jvm_flags = '-Djava.security.auth.login.config=$ZOOCFGDIR/jaas.conf '
  jvm_flags += '-Djava.security.krb5.conf=$ZOOCFGDIR/krb5.conf '

  bin_path = "%s/%s/%s-%s/bin" % (args.package_root,
    args.cluster, artifact, version)
  deploy_utils.append_to_file("%s/zkEnv.sh" % bin_path,
      'export JVMFLAGS="%s"\n' % jvm_flags)

def pack(args):
  get_zk_service_config(args)
  version = args.zookeeper_config.cluster.version
  deploy_utils.make_package_dir(args, "zookeeper", version)
  generate_client_config(args, "zookeeper", version)

  if not args.skip_tarball:
    deploy_utils.pack_package(args, "zookeeper", version)
  Log.print_success("Pack client utilities for zookeeper success!")

def rolling_update(args):
  get_zk_service_config(args)
  job_name = "zookeeper"

  if not args.skip_confirm:
    deploy_utils.confirm_action(args, "rolling_update")

  Log.print_info("Rolling updating %s" % job_name)
  hosts = args.zookeeper_config.jobs[job_name].hosts
  wait_time = 0
  for id in hosts.iterkeys():
    deploy_utils.confirm_rolling_update(id, wait_time)
    stop_job(args, hosts[id], job_name)
    deploy_utils.wait_for_job_stopping("zookeeper",
        args.zookeeper_config.cluster.name, job_name, hosts[id])
    start_job(args, hosts[id], job_name)
    deploy_utils.wait_for_job_starting("zookeeper",
        args.zookeeper_config.cluster.name, job_name, hosts[id])
    wait_time = args.time_interval
  Log.print_success("Rolling updating %s success" % job_name)

if __name__ == '__main__':
  test()
