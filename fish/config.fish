source /usr/share/cachyos-fish-config/cachyos-config.fish

function fish_greeting
    fastfetch
end
alias dotfiles='git --git-dir=/home/itscool2b/.dotfiles --work-tree=/home/itscool2b'

set -x PYENV_ROOT $HOME/.pyenv
fish_add_path $PYENV_ROOT/bin
pyenv init - | source

starship init fish | source

# Added by LM Studio CLI (lms)
set -gx PATH $PATH /home/itscool2b/.lmstudio/bin
# End of LM Studio CLI section

