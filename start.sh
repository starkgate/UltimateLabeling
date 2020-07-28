cd "/home/u42/UltimateLabeling_server"

tmux kill-session -t tracking_1
tmux kill-session -t tracking_2
tmux kill-session -t detection

source siamMask/env/bin/activate && tmux new -d -s tracking_1 "CUDA_VISIBLE_DEVICES=0 python -m tracker -p 8787"
source siamMask/env/bin/activate && tmux new -d -s tracking_2 "CUDA_VISIBLE_DEVICES=0 python -m tracker -p 8788"
source detection/env/bin/activate && tmux new -d -s detection "CUDA_VISIBLE_DEVICES=0 python -m detector"
