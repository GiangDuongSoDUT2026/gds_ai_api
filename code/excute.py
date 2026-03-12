import os
import glob
import json 
import pandas as pd
import tensorflow as tf
import transnetv2
import numpy as np
import cv2
import re
from multiprocessing import Process, Queue, Manager
import threading

# --- CONFIGURATION ---
# Lowered from 16 to 4 to prevent pipe crashes and VRAM exhaustion
NUM_WORKERS = 4   
keyframes_output_dir = '../output/Keyframes'
media_info_output_dir = '../output/media_info'
fps_jsonl_path = '../output/fps.jsonl' 

os.makedirs(keyframes_output_dir, exist_ok=True)
os.makedirs(media_info_output_dir, exist_ok=True)

def sanitize_filename(filename):
    """Removes special characters that break FFmpeg pipes and shell commands."""
    # Replace |, :, (, ), and spaces with underscores
    clean_name = re.sub(r'[\|: \(\)\[\]]', '_', filename)
    # Remove multiple underscores
    return re.sub(r'_+', '_', clean_name).strip('_')

def extract_high_res_frame(video_path, frame_index):
    """Extract a high-resolution frame from the video"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None

def worker_process(worker_id, gpu_id, task_queue, fps_lock):
    """Worker process assigned to a specific GPU."""
    os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu_id)
    
    # Configure TensorFlow memory growth
    gpus = tf.config.experimental.list_physical_devices('GPU')
    if gpus:
        try:
            for gpu in gpus:
                tf.config.experimental.set_memory_growth(gpu, True)
        except RuntimeError as e:
            print(f"Worker {worker_id} Error: {e}")
    
    # Initialize model
    model = transnetv2.TransNetV2()
    
    while True:
        video_path = task_queue.get()
        if video_path is None: 
            break
        
        try:
            process_video(video_path, model, fps_lock, worker_id)
        except Exception as e:
            print(f"Worker {worker_id}: Critical failure on {video_path}: {e}")
    
    print(f"Worker {worker_id}: Shutting down")

def process_video(video_path, model, fps_lock, worker_id):
    """Process a single video: detect shots and extract keyframes."""
    raw_name = os.path.splitext(os.path.basename(video_path))[0]
    video_name = sanitize_filename(raw_name) # Ensure safe paths
    expected_keyframes_dir = os.path.join(keyframes_output_dir, video_name)

    if os.path.isdir(expected_keyframes_dir):
        return
    
    print(f"Worker {worker_id} [GPU {os.environ.get('CUDA_VISIBLE_DEVICES')}]: Processing {video_name}")
    
    # Predict shots. 
    # Note: If FFmpeg still fails, ensure transnetv2.py isn't using too many threads.
    video_frames, single_frame_predictions, all_frame_predictions = model.predict_video(video_path)
    scenes = model.predictions_to_scenes(single_frame_predictions, threshold=0.01)
    
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    
    # Write FPS data (thread-safe)
    with fps_lock:
        with open(fps_jsonl_path, 'a') as f:
            json.dump({"videoname": video_name, "fps": fps}, f)
            f.write('\n')
    
    shot_data = []
    for i, (start, end) in enumerate(scenes):
        shot_data.append({
            'n': i,
            'frame_idx': start,
            'pts_time': start / fps if fps > 0 else 0,
            'fps': fps,
        })

    if not shot_data:
        return

    os.makedirs(expected_keyframes_dir, exist_ok=True)

    final_shots_with_paths = []
    for shot in shot_data:
        high_res_frame = extract_high_res_frame(video_path, shot['frame_idx'])
        if high_res_frame is not None:
            filename = f"shot_{shot['n']:04d}.jpg"
            filepath = os.path.join(expected_keyframes_dir, filename)
            cv2.imwrite(filepath, high_res_frame)
            shot['keyframe_path'] = filepath
        else:
            shot['keyframe_path'] = "failed"
        final_shots_with_paths.append(shot)

    # Save CSV media info
    media_info_filepath = os.path.join(media_info_output_dir, f"{video_name}.csv")
    pd.DataFrame(final_shots_with_paths).to_csv(media_info_filepath, index=False)

if __name__ == '__main__':
    mp4_files = glob.glob('/home/parrot/BKSmart/data/**/*.mp4', recursive=True)
    
    if os.path.exists(fps_jsonl_path):
        os.remove(fps_jsonl_path)
    
    manager = Manager()
    fps_lock = manager.Lock()
    task_queue = Queue()
    
    videos_to_process = []
    for path in mp4_files:
        v_name = sanitize_filename(os.path.splitext(os.path.basename(path))[0])
        if not os.path.isdir(os.path.join(keyframes_output_dir, v_name)):
            videos_to_process.append(path)
            task_queue.put(path)
    
    print(f"Starting processing for {len(videos_to_process)} videos with {NUM_WORKERS} workers.")

    for _ in range(NUM_WORKERS):
        task_queue.put(None)
    
    workers = []
    for i in range(NUM_WORKERS):
        gpu_id = i % 2 
        p = Process(target=worker_process, args=(i, gpu_id, task_queue, fps_lock))
        p.start()
        workers.append(p)
    
    for p in workers:
        p.join()

    print("Processing complete.")