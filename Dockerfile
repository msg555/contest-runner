# TODO: In order to run out container rootless we'll need to do a uid shift on 
# the /runners directory

{% set runners = dict(vars.runners.items() | selectattr("1.enabled")) -%}
{% macro offset_fs_ids(root, offset, root_id) -%}
find {{ root | shell_escape }} -exec python3 -c \
  'import os, sys; p = sys.argv[1]; st = os.lstat(p); os.lchown(p, st.st_uid + {{ offset }} if st.st_uid else {{ root_id }}, st.st_gid + {{ offset }} if st.st_gid else {{ root_id }})' {} ';'
{%- endmacro %}

###############################################################################
# Create an internal runner base image. This forms the basis of the environment
# that tasks will run in. This is just the same as the runner source image with
# a prebaked user added in.
FROM {{ vars.runner_source_image }} AS base-internal-runner

RUN groupadd -g 1000 taskrun \
 && useradd -u 1000 -g 1000 taskrun \
 && mkdir -p /task \
 && chown -R taskrun:taskrun /task


###############################################################################
# Install packages needed for each runner variant on top of the internal runner
# base image.
{% for runner_name, runner_data in runners.items() -%}
FROM base-internal-runner AS base-runner-{{ runner_name }}

RUN apt update \
 && apt install -y {% for package in runner_data.packages %} {{ package | shell_escape }}{% endfor %} \
 && rm -rf /var/lib/apt/lists/*

RUN {% for arg in runner_data.version %} {{ arg | shell_escape }}{% endfor %} > /version.txt 2>&1
{% endfor -%}


###############################################################################
# Create an intermediate image that we can use to produce overlay diffs for
# each of our runner variants. Setup the /lower dir with the source runner image
FROM python:3.10 AS base-diff-calculator

RUN pip install -U pip \
 && pip install uniondiff

COPY --from=base-internal-runner / /lower
RUN {{ offset_fs_ids("/lower", 100000, 1000) }}


###############################################################################
# Calculate the overlay diff for each runner variant.
{% for runner_name, runner_data in runners.items() -%}
FROM base-diff-calculator AS base-diff-calculator-{{ runner_name }}

COPY --from=base-runner-{{ runner_name }} / /merged

RUN {{ offset_fs_ids("/merged", 100000, 1000) }} \
 && uniondiff /merged /lower --output-type file -o /diff --preserve-owners
{% endfor -%}


###############################################################################
# Create a base image for the final artifacts. Copy in all the overlay diffs
# and setup the environment of source runner.
FROM {{ vars.runner_source_image }} AS base-source-runner

RUN mkdir /runners

COPY --from=base-diff-calculator /lower /runners/lower

{% for runner_name in runners -%}
COPY --from=base-diff-calculator-{{ runner_name }} /diff /runners/{{ runner_name }}
{% endfor -%}

RUN apt update \
 && apt install -y runc uidmap vim python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 taskrun-external \
 && useradd -u 1000 -g 1000 taskrun-external \
 && echo 'taskrun-external:100000:65536' > /etc/subuid \
 && echo 'taskrun-external:100000:65536' > /etc/subgid

###############################################################################
# Top level source-runner image
FROM base-source-runner AS source-runner

ENV PYTHONPATH=/sourcerunner/sourcerunner
WORKDIR /sourcerunner
COPY sourcerunner /sourcerunner/sourcerunner

COPY entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
