def test_package_exposes_version():
    import flir_seq_tool

    assert flir_seq_tool.__version__ == "0.1.0"
