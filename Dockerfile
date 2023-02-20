FROM python:3.10 AS diff-calculator

RUN pip install -U pip \
 && pip install uniondiff

COPY --from={{ vars.runner_base }} / /lower


FROM {{ vars.runner_base }} AS runner

RUN apt update

{% for runner_name, runner_data in vars.runners.items() -%}
FROM runner AS runner-{{ runner_name }}

RUN apt install -y {% for package in runner_data.packages %} {{ package | shell_escape }}{% endfor %} \
 && rm -rf /var/lib/apt/lists/*

RUN {% for arg in runner_data.version %} {{ arg | shell_escape }}{% endfor %} > /version.txt 2>&1

FROM diff-calculator AS diff-calculator-{{ runner_name }}

COPY --from=runner-{{ runner_name }} / /merged

RUN uniondiff /merged /lower --output-type tgz > /diff.tgz
{% endfor -%}


FROM {{ vars.runner_base }} AS source-runner

RUN mkdir /runners

{% for runner_name in vars.runners -%}
COPY --from=diff-calculator-{{ runner_name }} /diff.tgz /runners/{{ runner_name }}.tgz
COPY --from=runner-{{ runner_name }} /version.txt /runners/{{ runner_name }}.txt
{% endfor -%}
