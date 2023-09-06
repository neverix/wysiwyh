if __name__=="__main__":
  import collections
  import queue
  import threading
  import numpy as np
  import sys
  from datetime import datetime
  import os
  import time
  from glob import glob
  import argparse
  import multiprocessing
  import math
  import tensorflow as tf
  # tf.config.experimental.set_visible_devices([], 'GPU')
  import cv2
  from model import Autoencoder, process_img, convert_to_tf, CustomCallback
#   import pyaudio
  import plotting

  img_size = 64
  batch_size = 64
  n_steps = 100000
  train = False

  # schedule = tf.keras.optimizers.schedules.CosineDecay(1.0, n_steps)
  # optimizer = tf.keras.optimizers.Adam()
  # optimizer = tf.keras.optimizers.SGD(learning_rate=schedule)
  # optimizer = tf.keras.optimizers.SGD(learning_rate=1e-1)
  optimizer = tf.keras.optimizers.SGD(learning_rate=1e-1)

  # autoencoder = Autoencoder(100, 7, batch_size, img_size)
  # autoencoder = Autoencoder(100, 5, batch_size, img_size)
  autoencoder = Autoencoder(128, batch_size, img_size)
  autoencoder.compile(optimizer=optimizer, run_eagerly=True)

  def randomize_phase(absolute_values):
    random_angles = np.random.uniform(low=0, high=np.array(2*np.pi).repeat(upper_limit_hz/fps+1))
    real_part = absolute_values * np.cos(random_angles)
    imag_part = absolute_values * np.sin(random_angles)
    complex_results = real_part + 1j * imag_part
    assert np.isclose(np.abs(complex_results), absolute_values).all()
    return complex_results

  # os.makedirs("figures", exist_ok=True)

  # parser = argparse.ArgumentParser(description="Either train a model, evaluate an existing one on a dataset or run live.")
  # parser.add_argument('--mode', type=str, default="train", help='"train" or "live"')
  # parser.add_argument('--video_source', type=str, default="0", help='"0" for internal camera or URL or path to video file.')
  # parser.add_argument('--weights', type=str, default=None, help='Path to weights of the neural network. For example: "logs/20210829-133633/weights.1799-0.00745/variables/variables"')
  # parser.add_argument('--data_dir', type=str, default='data3', help='Directory with training data. Only relevant for training.')

  parser = argparse.ArgumentParser(description="Either train a model, evaluate an existing one on a dataset or run live.")
  parser.add_argument('--mode', type=str, default="live", help='"train" or "live"')
  parser.add_argument('--video_source', type=str, default="work.mov", help='"0" for internal camera or URL or path to video file.')
  parser.add_argument('--weights', type=str)  # , default='logs/20220211-174349/weights.3971-0.00510/variables/variables')
  parser.add_argument('--data_dir', type=str, default=None, help='Directory with training data. Only relevant for training.')

  args = parser.parse_args()
  print("Got these arguments:", args)
  if args.weights:
    autoencoder.load_weights(args.weights)

  if args.mode=='train':

    x_files = sorted(glob(f'{args.data_dir}/*.jpg'))
    files_ds = tf.data.Dataset.from_tensor_slices(x_files)
    raw_ds = files_ds.map(lambda x: process_img(x, img_size)).cache()

    train_items = int(len(raw_ds) * 0.9)
    train_ds = raw_ds.take(train_items).shuffle(10000 ,reshuffle_each_iteration=True).batch(batch_size)
    training_data = train_ds.take(math.floor(train_items/batch_size))
    batches_per_epoch = len(training_data)
    val_ds = raw_ds.skip(train_items).shuffle(10000,reshuffle_each_iteration=False, seed=0).take(batch_size).batch(batch_size)
    val_batch = next(iter(val_ds))

    logdir = "logs/" + datetime.now().strftime("%Y%m%d-%H%M%S")
    file_writer = tf.summary.create_file_writer(logdir)

    with file_writer.as_default():
      tf.summary.image("In imgs", val_batch, step=0, max_outputs=8)

    epochs_to_train = int(n_steps/batches_per_epoch)

    autoencoder.fit(training_data,
                    epochs=epochs_to_train,
                    callbacks=[
                      CustomCallback(file_writer, val_batch), 
                      tf.keras.callbacks.ModelCheckpoint(
                        os.path.join(logdir, "weights.{epoch:02d}-{total_loss:.5f}"), monitor='total_loss', verbose=1, save_best_only=False,
                        save_weights_only=False, mode='min', save_freq=int(n_steps/100)
                      )],
                    shuffle=False)

  elif args.mode=="live":

    fps = 10
    upper_limit_hz = 5000
    volume = 1
    _video_file_speed_multiplier = 1

    buffer_size = int(2*upper_limit_hz/fps)

    video_source = args.video_source
    is_file = True
    try: 
      video_source = int(args.video_source)
      is_file = False
    except ValueError:
      pass

    print("is_file", is_file)

    cap = cv2.VideoCapture(video_source)
    if is_file:
      print("file_fps", round(cap.get(cv2.CAP_PROP_FPS), 2))
      # inter_frame_time = 1/cap.get(cv2.CAP_PROP_FPS)
      inter_frame_time = 1/fps

    plotting_queue = multiprocessing.Queue()

    plotting_process = multiprocessing.Process(target=plotting.plotting_function, args=(plotting_queue,))
    plotting_process.start()

    last_computation_end_time = None

    def get_frame():
      global last_computation_end_time
      global plotting_queue
      start_time = time.time()

      if is_file:
        accumulated_inter_frame_time = 0
        while accumulated_inter_frame_time < 1/fps/_video_file_speed_multiplier:
          if not cap.isOpened():
            quit() 
          success, img = cap.read()
          if not success:
            quit()

          accumulated_inter_frame_time += inter_frame_time
      else:
        success, img = cap.read()
        if not success:
          quit()

      img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

      new_width = int(4/3*img.shape[0])
      offset = int((img.shape[1]-new_width)/2)

      if img.shape[1]/img.shape[0]*3 != 4:
        img = img[:, offset:offset+new_width, :]
      assert img.shape[1]/img.shape[0]*3 == 4, f'{img.shape}'
      if img.shape[1] > 320:
        img = cv2.resize(img, (320, 240))

      converted_img = convert_to_tf(img, img_size)
      predict_ds = converted_img[None,:,:,:]

      output_img, predicted_code = autoencoder(predict_ds, training=True, eval=True)

      current_code = predicted_code[0,:].numpy().astype(np.float64)
      output_img = output_img[0,...].numpy()

      new_size = int(upper_limit_hz/fps)
      current_code_repeated = []
      items_per_element = new_size/current_code.shape[0]
      limits = (np.arange(current_code.shape[0])+1) * items_per_element

      current_item = 0
      s = time.time()
      for i in range(new_size):
        current_code_repeated.append(current_code[current_item])
        if i > limits[current_item]:
          current_item += 1
      
      current_code_filled_up = np.concatenate((np.zeros(1), current_code_repeated))

      time_series = np.fft.irfft(randomize_phase(current_code_filled_up)).astype(np.float32)
      assert len(time_series) == buffer_size
      audio_to_be_played = time_series.tobytes()

      # plotting_queue.put((converted_img.numpy(), current_code, output_img))
      plotting_queue.put((img, current_code, output_img))

      computation_end_time = time.time()
      last_computation_duration = computation_end_time - start_time

      total_diff = computation_end_time - last_computation_end_time if last_computation_end_time is not None else 0
      last_computation_end_time = computation_end_time
      print(f"Fraction of allotted time: {(last_computation_duration)/(1/fps):.3f}, computation time: {last_computation_duration:.3f}, total active time: {last_computation_duration:.3f}, total time: {total_diff:.3f}", end="\r")
      sys.stdout.flush()
      return audio_to_be_played

    chunks_per_frame = 10

    process_new_frame_queue = queue.Queue()
    audio_chunk_queue = collections.deque()

    def get_and_enqueue_new_frame():
      audio_frame = get_frame()
      for i in range(chunks_per_frame):
        chunk_len = int(len(audio_frame)/chunks_per_frame)
        audio_chunk_queue.append(audio_frame[i*chunk_len:(i+1)*chunk_len])

    def produce_new_frame():
      while True: 
        process_new_frame_queue.get(block=True, timeout=1)
        get_and_enqueue_new_frame()

    n_callback_called = 0
    def cb(in_data, frame_count, time_info, status):
      global n_callback_called
      if len(audio_chunk_queue) == 0:
        # Only necessary at the beginning hopefully
        if n_callback_called != 0:
          print('Underflow of audio queue occurred!')
        get_and_enqueue_new_frame()
      # elif len(audio_chunk_queue) == int(chunks_per_frame/2):
      elif len(audio_chunk_queue) == chunks_per_frame-1:
        process_new_frame_queue.put_nowait(True)
        n_callback_called += 1
      current_item = audio_chunk_queue.popleft()
      return (current_item, pyaudio.paContinue)
        
    new_frame_thread = threading.Thread(target=produce_new_frame, args=())
    new_frame_thread.start()

    p = pyaudio.PyAudio()
    # for paFloat32 sample values must be in range [-1.0, 1.0]
    stream = p.open(format=pyaudio.paFloat32,
                    frames_per_buffer=int(buffer_size/chunks_per_frame),
                    channels=1,
                    rate=2*upper_limit_hz,
                    output=True,
                    stream_callback=cb)

    print(f"audio latency: {stream.get_output_latency():.2f}")

    stream.start_stream()

    plotting_process.join()
