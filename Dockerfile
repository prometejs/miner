FROM python:3.13-slim

# The build context has no .git, so the version is injected by the workflow
# (setuptools-scm reads SETUPTOOLS_SCM_PRETEND_VERSION).
ARG VERSION=0.0.0+docker
ENV SETUPTOOLS_SCM_PRETEND_VERSION=$VERSION

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 3333 3334
CMD ["prometejs-miner"]
