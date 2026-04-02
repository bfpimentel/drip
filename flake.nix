{
  description = "drip devshell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        buildInputs = with pkgs; [
          uv
          python312
        ];

        shellHook = ''
          if [ ! -d ".env" ]; then
            source .env
          fi

          if [ ! -d ".venv" ]; then
            uv venv .venv
          fi

          source .venv/bin/activate
          uv sync

          echo "drip development environment"
          echo ""
          echo "Available commands:"
          echo "  - uv sync                  # Install dependencies"
          echo "  - uv run python main.py    # Start the server (direct)"
          echo "  - uv run flask run         # Start the server (flask CLI)"
          echo ""
        '';
      };
    };
}
