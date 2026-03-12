class Oppy < Formula
  desc "OPPY - Mission Control for Proxy Links"
  homepage "https://github.com/f4rih/oppy"
  url "https://github.com/f4rih/oppy/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_RELEASE_TARBALL_SHA256"
  license "MIT"

  depends_on "python@3.12"
  depends_on "xray"

  def install
    python = Formula["python@3.12"].opt_bin/"python3.12"
    system python, "-m", "pip", "install", *std_pip_args, "."
  end

  test do
    system bin/"oppy", "--help"
  end
end

# Notes:
# 1) Replace homepage/url/sha256 with your real repository + release checksum.
# 2) Put this formula in your tap repo under Formula/oppy.rb.
# 3) Then users can install with:
#    brew tap <you>/<tap>
#    brew install oppy
