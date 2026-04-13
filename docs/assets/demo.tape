# VHS tape file — generates docs/assets/demo.gif
# Install: brew install charmbracelet/tap/vhs
# Run:     cd l3-data-agent && vhs docs/assets/demo.tape

Output docs/assets/demo.gif
Set Shell zsh
Set FontSize 14
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

# Setup: activate venv and set env
Hide
Type "source .venv/bin/activate && export ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
Enter
Sleep 1s
Show

# Show the command
Type "l3-agent --config examples/demo/config.yaml"
Sleep 500ms
Enter
Sleep 3s

# Ask the question
Type "What caused the DAU drop around March 14?"
Sleep 500ms
Enter

# Wait for the analysis to complete (adjust timing based on actual run)
Sleep 90s

# Show the result for a few seconds
Sleep 5s

# Exit
Type "exit"
Enter
Sleep 1s
