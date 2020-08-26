import setuptools  # type: ignore

setuptools.setup(
    name='minimalci',
    version='0.1',
    packages=["minimalci"],
    py_modules=["dssh"],
    entry_points={
        'console_scripts': [
            'dssh=dssh:main',
        ],
    },
)
