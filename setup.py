"""
Setup script for fastapi-gradual-throttle.

This is a thin shim kept for editable installs and legacy tooling.
The canonical config lives in pyproject.toml.
"""

from setuptools import find_packages, setup


def read_readme():
    with open("README.md", "r", encoding="utf-8") as f:
        return f.read()


setup(
    name="fastapi-gradual-throttle",
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
    description=(
        "FastAPI rate limiting and throttling middleware — gradual delay, "
        "strict 429, combined mode, Redis backend, per-route overrides"
    ),
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    author="Dhruv Patel",
    author_email="pateldhruvn2004@gmail.com",
    maintainer="Dhruv Patel",
    maintainer_email="pateldhruvn2004@gmail.com",
    url="https://github.com/DhruvSimform/fastapi_gradual_throttle",
    packages=find_packages(include=["fastapi_gradual_throttle*"]),
    include_package_data=True,
    install_requires=[
        "fastapi>=0.68",
        "starlette>=0.20",
        "pydantic-settings>=2.0",
    ],
    extras_require={
        "redis": ["redis>=4.2"],
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "httpx>=0.24",
            "pytest-cov>=4.0",
            "ruff>=0.1",
            "mypy>=1.0",
        ],
    },
    python_requires=">=3.10",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Framework :: FastAPI",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Internet :: WWW/HTTP :: HTTP Servers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Networking",
        "Typing :: Typed",
    ],
    keywords=(
        "fastapi starlette middleware throttling rate-limiting rate-limiter "
        "gradual delay api-throttling request-throttling fastapi-middleware "
        "fastapi-rate-limit python-rate-limiter redis-rate-limit token-bucket "
        "sliding-window abuse-prevention api-protection"
    ),
    project_urls={
        "Homepage": "https://github.com/DhruvSimform/fastapi_gradual_throttle",
        "Documentation": "https://github.com/DhruvSimform/fastapi_gradual_throttle#readme",
        "Bug Reports": "https://github.com/DhruvSimform/fastapi_gradual_throttle/issues",
        "Source": "https://github.com/DhruvSimform/fastapi_gradual_throttle",
        "Changelog": "https://github.com/DhruvSimform/fastapi_gradual_throttle#changelog",
    },
)
