import multiprocessing, concurrent.futures
import time, datetime, copy, requests, math, os, platform
import queue as Queue

# Sample callback function
def callback_sample(id: int, config=None, task=None):
    if id == 1:
        print("Runing sample task function, please customize yours according to the actual usage.")
    result = {
        'taskId': id
    }
    return result

# Sample result callback function
def result_callback_sample(id: int, config=None, result=None, log: dict=None):
    if id == 1:
        print("Runing sample result function, please customize yours according to the actual usage.")
    return result

# Default configuration
default_config = {
    'debug': False,
    'task': {
        'list': [],                     # Support list and integer. Integer represent the number of tasks to be generated.
        'callback': callback_sample,
        'config': {},
        'result_callback': False
    },
    'worker': {
        'number': multiprocessing.cpu_count(),
        'frequency_mode': {             # Changing from assigning tasks to a fixed number of workers once, to assigning tasks and workers frequently.
            'enabled': False, 
            'interval': 1,              # The second(s) of interval.
            'accumulated_workers': 0,   # Accumulate the number of workers for each interval for next dispatch.
            'max_workers': None,        # limit the maximum number of workers to prevent system exhaustion.
        },
        'use_processing': False,        # To break GIL, workers will be based on processing pool.
        'parallel_processing': {        # To break GIL and require a number of workers greater than the number of CPU cores.
            'enabled': False,           # `worker.use_processing` setting will be ignored when enabled. The actual number of workers will be adjusted to a multiple of the CPU core count.
            'use_queue': False,         # Enable a task queue to specify the number of workers without adjustment, though the maximum may be limited by your device.
        },   
    },
    'runtime': None,                    # Dispatcher max runtime in seconds.
    'verbose': True
}

# Global variable
results = []
logs = []
result_info = {}

# Start function
def start(user_config: dict) -> list:

    global results, logs, result_info
    results = []
    logs = []

    # Merge config with 2 level
    config = _merge_dicts_recursive(default_config, user_config)
    # config = copy.deepcopy(default_config)
    # for level1_key in config.keys():
    #     if level1_key in user_config:
    #         if isinstance(config[level1_key], dict):
    #             config[level1_key].update(user_config[level1_key])
    #         else:
    #             config[level1_key] = user_config[level1_key]

    # Multi-processing handler
    use_processing = config['worker']['use_processing']
    parallel_processing = config['worker']['parallel_processing']['enabled']
    pp_use_queue = config['worker']['parallel_processing']['use_queue']
    if use_processing or parallel_processing:
        in_child_process = (multiprocessing.current_process().name != 'MainProcess')
        # Return False if is in worker process to let caller handle
        if in_child_process:
            # print("Exit procedure due to the child process")
            return False
        
    # Debug mode
    if config['debug']:
        print("Configuration Dictionary:")
        print(config)

    # Callback check
    if not callable(config['task']['callback']):
        exit("Callback function is invalied")

    # Task list to queue
    task_list = []
    user_task_list = config['task']['list']
    if isinstance(user_task_list, list):
        id = 1
        for task in user_task_list:
            data = {
                'id': id,
                'task': task
            }
            task_list.append(data)
            id += 1
    elif isinstance(user_task_list, int):
        for i in range(user_task_list):
            id = i + 1
            data = {
                'id': id,
                'task': {}
            }
            task_list.append(data)

    # Worker dispatch
    worker_num = config['worker']['number']
    worker_num = int(worker_num) if isinstance(worker_num, int) else 1
    frequency_interval_seconds = float(config['worker']['frequency_mode']['interval']) if config['worker']['frequency_mode']['enabled'] else 0
    frequency_max_workers = config['worker']['frequency_mode']['max_workers']
    frequency_max_workers = frequency_max_workers if frequency_interval_seconds and isinstance(frequency_max_workers, int) else 0
    accumulated_workers = math.floor(config['worker']['frequency_mode']['accumulated_workers']) if config['worker']['frequency_mode']['accumulated_workers'] else 0
    max_workers = min(frequency_max_workers if frequency_max_workers else len(task_list) if frequency_interval_seconds else worker_num, 32766)
    local_cpu_count = multiprocessing.cpu_count()
    pool_max_worker = max_workers
    if parallel_processing:
        pool_max_worker = worker_num if worker_num < local_cpu_count else local_cpu_count
        pp_adjusted_worker_num = 0
        pp_adjusted_accumulated_worker_num = 0
        pp_workers_count = pool_max_worker
        pp_remaining_workers_count = 0
        pp_thread_max_workers = 1
        pp_remaining_thread_max_workers = 0
        if not pp_use_queue:
            pp_thread_workers = 1
            pp_thread_accumulated_workers = 0
            # Adjust the number of workers for each process call
            if worker_num >= pool_max_worker:
                pp_adjusted_worker_num = worker_num % pool_max_worker
                if pp_adjusted_worker_num != 0:
                    worker_num -= pp_adjusted_worker_num
                pp_thread_max_workers = pp_thread_workers = int(worker_num / pool_max_worker)
            # Adjust the number of accumulated workers for each process call
            if accumulated_workers:
                pp_adjusted_accumulated_worker_num = accumulated_workers % pool_max_worker
                if pp_adjusted_accumulated_worker_num != 0:
                    accumulated_workers -= pp_adjusted_accumulated_worker_num
                pp_thread_accumulated_workers = int(accumulated_workers / pool_max_worker)
        elif pp_use_queue: 
            # Queue connection protection for non-Linux OS.
            max_workers = min(128, max_workers) if os.name != 'posix' or platform.system() != 'Linux' else max_workers

            # pp_workers_count = pool_max_worker
            # pp_remaining_workers_count = 0
            # pp_thread_max_workers = 1
            # pp_remaining_thread_max_workers = 0
            # Calculate workers vs cpu cores
            if worker_num % pool_max_worker == 0:
                pp_thread_max_workers = int(worker_num / pool_max_worker)
            else:
                # Separate one from the processes for the remaining workers
                print("Sdf")
                pp_workers_count = local_cpu_count - 1
                pp_remaining_workers_count = 1
                quotient, remainder = divmod(worker_num, pp_workers_count)
                pp_thread_max_workers = int(quotient)
                pp_remaining_thread_max_workers = int(remainder)
            # max_workers = (pp_workers_count * pp_thread_max_workers) + (pp_remaining_workers_count * pp_remaining_thread_max_workers)
            # print("pool_max_worker: {}, Max workers: {} = {}p x {}t + {}p x {}t".format(
            #     pool_max_worker, 
            #     max_workers,
            #     pp_workers_count, 
            #     pp_thread_max_workers,
            #     pp_remaining_workers_count,
            #     pp_remaining_thread_max_workers
            #     ))
        # Common Parellel Setting
        if frequency_interval_seconds:
            pp_thread_max_workers = math.floor(max_workers / pool_max_worker)
            max_workers = (pp_workers_count * pp_thread_max_workers) + (pp_remaining_workers_count * pp_remaining_thread_max_workers)

    runtime = float(config['runtime']) if config['runtime'] else None
    if config['verbose']:
        print("\nWorker Dispatcher Configutation:")
        print("- Local CPU core: {}".format(local_cpu_count))
        print("- Tasks Count: {}".format(len(task_list)))
        print("- Runtime: {}".format("{} sec".format(runtime) if runtime else "Unlimited"))
        print("- Dispatch Mode: {}".format("Frequency Mode" if frequency_interval_seconds else "Fixed Workers (Default)"))
        if frequency_interval_seconds:
            print("  └ Interval Seconds: {}".format(frequency_interval_seconds))
            print("  └ Accumulated Workers: {}{}".format(accumulated_workers, " (Adjusted from {})".format(accumulated_workers + pp_adjusted_accumulated_worker_num) if parallel_processing and pp_adjusted_accumulated_worker_num else ""))
        print("- Workers Info:")
        print("  └ Worker Type:", "Parallel Processing with {} process(es)".format(pool_max_worker) if parallel_processing else "Processing" if use_processing else "Threading")
        if parallel_processing:
            print("  └ Task Queue: {}".format("On" if pp_use_queue else "Off"))
        print("  └ Number of Workers : {}{}".format(worker_num, " (Adjusted from {})".format(worker_num + pp_adjusted_worker_num) if parallel_processing and pp_adjusted_worker_num else ""))
        output_parellel_max_worker_info = "{} ({}p x {}t{})".format(max_workers, pp_workers_count, pp_thread_max_workers, " + {}p x {}t".format(pp_remaining_workers_count, pp_remaining_thread_max_workers) if pp_remaining_workers_count else "") if parallel_processing else None
        print("  └ Max Worker: {}".format(output_parellel_max_worker_info if output_parellel_max_worker_info else max_workers))
    pool_executor_class = concurrent.futures.ProcessPoolExecutor if use_processing or parallel_processing else concurrent.futures.ThreadPoolExecutor
    result_info['started_at'] = time.time()
    datetime_timezone_obj = datetime.timezone(datetime.timedelta(hours=time.localtime().tm_gmtoff / 3600))
    if config['verbose']: print("\n--- Start to dispatch workers at {} ---\n".format(datetime.datetime.fromtimestamp(result_info['started_at'], datetime_timezone_obj).isoformat()))

    # Pool Executor
    with pool_executor_class(max_workers=pool_max_worker) as executor:
        undispatched_tasks_count = 0
        pool_results = []
        per_second_remaining_quota = worker_num
        per_second_remaining_runtime = runtime
        per_second_last_time = time.time()
        # Task dispatch

        # Parellel
        if parallel_processing and not pp_use_queue:

            pool_results = []
            pp_workers_count = pool_max_worker

            chunk_size = pool_max_worker
            chunks = [task_list[i::chunk_size] for i in range(chunk_size)]
            # print(chunks);exit()

            for tasks in chunks:
                pool_result = executor.submit(_consume_tasks, tasks, config, 
                                              runtime = runtime, 
                                              max_worker = pp_thread_max_workers,
                                              use_processing = False,
                                              worker_num = pp_thread_workers,
                                              frequency_interval_seconds = frequency_interval_seconds,
                                              accumulated_workers = pp_thread_accumulated_workers
                                              )
                pool_results.append(pool_result)
            
            # Cancel runtime for process pool and let comsume queue thread stop
            runtime = None

        elif parallel_processing and pp_use_queue:

            # Queue method
            queue = multiprocessing.Manager().Queue()
            if not frequency_interval_seconds:
                for task in task_list:
                    queue.put(task)
            # for i in range(max_workers):
            #     queue.put(None)
            pool_results = []
            pp_workers_count = pool_max_worker
            # Addition process
            if pp_remaining_thread_max_workers:
                pool_result = executor.submit(_parallel_consume_tasks, queue, config, runtime, pp_remaining_thread_max_workers, frequency_interval_seconds)
                pool_results.append(pool_result)
                pp_workers_count -= 1
            # Genral process(es)
            for i in range(pp_workers_count):
                pool_result = executor.submit(_parallel_consume_tasks, queue, config, runtime, pp_thread_max_workers, frequency_interval_seconds)
                pool_results.append(pool_result)

            if frequency_interval_seconds:
                s = 0
                while True:
                    e = s + worker_num
                    tasks_batch = task_list[s:e]
                    s = e
                    if accumulated_workers:
                        worker_num += accumulated_workers
                    if len(tasks_batch) == 0:
                        break
                    for task in tasks_batch:
                        queue.put(task)
                    # Executed time consideration
                    passed_time = abs(time.time() - per_second_last_time)
                    sleep_time = frequency_interval_seconds - passed_time if frequency_interval_seconds > passed_time else 0
                    time.sleep(float(sleep_time))
                    per_second_last_time = time.time()
                    # if runtime and (per_second_remaining_runtime := per_second_remaining_runtime - frequency_interval_seconds) <= 0:
                    if runtime:
                        per_second_remaining_runtime = per_second_remaining_runtime - max(frequency_interval_seconds, passed_time)
                        if per_second_remaining_runtime <= 0:
                            undispatched_tasks_count = len(task_list[e:])
                            if config['verbose']: print(f'Dispatcher timeout reached, {undispatched_tasks_count} remaining tasks were abandoned')
                            break
                # Close threads by giving empty jobs
                for i in range(max_workers):
                    queue.put(None)

            # Cancel runtime for process pool and let comsume queue thread stop
            runtime = None

            # print(pool_results)
            # chunk_size = len(task_list) // pool_max_worker + (len(task_list) % pool_max_worker > 0)
            # chunks = [task_list[i:i + chunk_size] for i in range(0, len(task_list), chunk_size)]

            # Old method
            # chunk_size = pool_max_worker
            # chunks = [task_list[i::chunk_size] for i in range(chunk_size)]
            # print(chunk_size, chunks);exit()
            # pool_results = {executor.submit(_parallel__consume_tasks, tasks, config, runtime, pp_thread_max_workers): tasks for tasks in chunks}
            # additional_results = executor.submit(_parallel__consume_tasks, tasks, config, runtime, pp_remaining_thread_max_workers)
            # pool_results.update(additional_results)
        
        else:
            for i, task in enumerate(task_list):
                pool_result = executor.submit(_consume_task, task, config)
                pool_results.append(pool_result)
                # Worker per_second setting
                # if frequency_interval_seconds and (per_second_remaining_quota := per_second_remaining_quota - 1) <= 0:
                if frequency_interval_seconds:
                    per_second_remaining_quota -= 1
                    if per_second_remaining_quota <= 0:
                        if accumulated_workers:
                            worker_num += accumulated_workers
                        per_second_remaining_quota = worker_num
                        # Executed time consideration
                        passed_time = abs(time.time() - per_second_last_time)
                        sleep_time = frequency_interval_seconds - passed_time if frequency_interval_seconds > passed_time else 0
                        time.sleep(float(sleep_time))
                        per_second_last_time = time.time()
                        # Max Runtime setting
                        # if runtime and (per_second_remaining_runtime := per_second_remaining_runtime - frequency_interval_seconds) <= 0:
                        if runtime:
                            per_second_remaining_runtime = per_second_remaining_runtime - max(frequency_interval_seconds, passed_time)
                            if per_second_remaining_runtime <= 0:
                                undispatched_tasks_count = len(task_list) - (i + 1)
                                if config['verbose']: print(f'Dispatcher timeout reached, {undispatched_tasks_count} remaining tasks were abandoned')
                                break

        # Wait for the pool to complete or timeout
        done, not_done = concurrent.futures.wait(
            pool_results,
            timeout=runtime,
            return_when=concurrent.futures.ALL_COMPLETED
        )
        # Cancel remaining tasks if the timeout was reached
        if not_done:
            if config['verbose']: print(f'Dispatcher timeout reached, cancelling {len(not_done)} remaining tasks...')
            for future in not_done:
                future.cancel()

        # Get results from the async results
        for future in concurrent.futures.as_completed(done):
            
            if parallel_processing:
                try:
                    chunk_result = future.result()
                    # print(chunk_result)
                    chunk_log = chunk_result['log_list']
                except Exception as e:
                    break
                    exit(f"Fatal error occurred in task.callback function: {e}")
                # print(chunk_log);exit
                for log in chunk_log:
                    result = log['result']
                    if callable(config['task']['result_callback']):
                        try:
                            result = config['task']['result_callback'](config=config['task']['config'], id=log['task_id'], result=result, log=log)
                        except Exception as e:
                            exit(f"Fatal error occurred in task.result_callback function: {e}")
                    logs.append(log)
                    results.append(result)
                
                if pp_use_queue and not undispatched_tasks_count:
                    undispatched_tasks_count = 0
                    # Check whether there are still any empty marks in the queue.
                    try:
                        task = queue.get_nowait()
                        if task is not None:
                            undispatched_tasks_count = queue.qsize() + 1
                    except Queue.Empty:
                        pass
                else:
                    # Sum up the number of undispatched tasks
                    undispatched_tasks_count += chunk_result['not_done_count']
                
            else:
                try:
                    log = future.result()
                except Exception as e:
                    exit(f"Fatal error occurred in task.callback function: {e}")
                result = log['result']
                if callable(config['task']['result_callback']):
                    try:
                        result = config['task']['result_callback'](config=config['task']['config'], id=log['task_id'], result=result, log=log)
                    except Exception as e:
                        exit(f"Fatal error occurred in task.result_callback function: {e}")
                logs.append(log)
                results.append(result)
        # results = [result.result() for result in concurrent.futures.as_completed(pool_results)]

    result_info['ended_at'] = time.time()
    result_info['duration'] = result_info['ended_at'] - result_info['started_at']
    if config['verbose']:
        print("\n--- End of worker dispatch at {}---\n".format(datetime.datetime.fromtimestamp(result_info['ended_at'], datetime_timezone_obj).isoformat()))
        print("Spend Time: {:.6f} sec".format(result_info['duration']))
        completed_task_count = len(results) if parallel_processing else len(done)
        print("Completed Tasks Count: {}".format(completed_task_count))
        uncompleted_task_count = 0 if parallel_processing else len(not_done)
        print("Uncompleted Tasks Count: {}".format(uncompleted_task_count))
        print("Undispatched Tasks Count: {}".format(undispatched_tasks_count))
    return results

# Deal with tasks
def _consume_tasks(task_list, config, 
                   max_worker=1, 
                   runtime=None,
                   use_processing=False, 
                   worker_num=1,
                   frequency_interval_seconds=False, 
                   accumulated_workers=0
                   ) -> list:
    
    per_second_remaining_runtime = runtime
    pool_results = []
    undispatched_tasks_count = 0
    pool_executor_class = concurrent.futures.ProcessPoolExecutor if use_processing else concurrent.futures.ThreadPoolExecutor
    with pool_executor_class(max_workers=max_worker) as executor:
        per_second_last_time = time.time()
        per_second_remaining_quota = worker_num
        # Each task
        for i, task in enumerate(task_list):
            pool_result = executor.submit(_consume_task, task, config)
            pool_results.append(pool_result)
            # Worker per_second setting
            # if frequency_interval_seconds and (per_second_remaining_quota := per_second_remaining_quota - 1) <= 0:
            if frequency_interval_seconds:
                per_second_remaining_quota -= 1
                if per_second_remaining_quota <= 0:
                    if accumulated_workers:
                        worker_num += accumulated_workers
                    per_second_remaining_quota = worker_num
                    # Executed time consideration
                    passed_time = abs(time.time() - per_second_last_time)
                    sleep_time = frequency_interval_seconds - passed_time if frequency_interval_seconds > passed_time else 0
                    time.sleep(float(sleep_time))
                    per_second_last_time = time.time()
                    # Max Runtime setting
                    # if runtime and (per_second_remaining_runtime := per_second_remaining_runtime - frequency_interval_seconds) <= 0:
                    if runtime:
                        # print(sleep_time)
                        per_second_remaining_runtime = per_second_remaining_runtime - max(frequency_interval_seconds, passed_time)
                        if per_second_remaining_runtime <= 0:
                            undispatched_tasks_count = len(task_list) - (i + 1)
                            if config['verbose']: print(f'Dispatcher timeout reached, {undispatched_tasks_count} remaining tasks were abandoned')
                            break

        # Wait for the pool to complete or timeout
        done, not_done = concurrent.futures.wait(
            pool_results,
            timeout=runtime,
            return_when=concurrent.futures.ALL_COMPLETED
        )
        # Cancel remaining tasks if the timeout was reached
        if not_done:
            if config['verbose']: print(f'Dispatcher timeout reached, cancelling {len(not_done)} remaining tasks...')
            for future in not_done:
                future.cancel()

        for future in concurrent.futures.as_completed(done):
            try:
                log = future.result()
            except Exception as e:
                exit(f"Fatal error occurred in task.callback function: {e}")
            if callable(config['task']['result_callback']):
                try:
                    log['result'] = config['task']['result_callback'](config=config['task']['config'], id=log['task_id'], result=log['result'], log=log)
                except Exception as e:
                    exit(f"Fatal error occurred in task.result_callback function: {e}")
            logs.append(log)

    undispatched_tasks_count = undispatched_tasks_count if undispatched_tasks_count else len(not_done)

    return {'log_list': logs, 'done_count': len(done), 'not_done_count': undispatched_tasks_count}

# Each parallel porcess (Queue)
def _parallel_consume_tasks(queue, config, runtime, worker_num, frequency_interval_seconds) -> dict:
    logs = []
    timeout_unixtime = time.time() + runtime if runtime else None
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_num) as executor:

        pool_results = []
        for i in range(1, int(worker_num) + 1):
            pool_result = executor.submit(_consume_queue, queue, config, timeout_unixtime, frequency_interval_seconds)
            pool_results.append(pool_result)

        # Wait for the pool to complete or timeout
        done, not_done = concurrent.futures.wait(
            pool_results,
            timeout=None,
            return_when=concurrent.futures.ALL_COMPLETED
        )
        # Cancel remaining tasks if the timeout was reached
        if not_done:
            if config['verbose']: print(f'Dispatcher timeout reached, cancelling {len(not_done)} remaining tasks...')
            for future in not_done:
                future.cancel()

        for future in concurrent.futures.as_completed(done):
            try:
                log_batch = future.result()
            except Exception as e:
                exit(f"Fatal error occurred in task.callback function in thread pool: {e}")
            # print(log)
            for log in log_batch:
                if callable(config['task']['result_callback']):
                    try:
                        log['result'] = config['task']['result_callback'](config=config['task']['config'], id=log['task_id'], result=log['result'], log=log)
                    except Exception as e:
                        exit(f"Fatal error occurred in task.result_callback function: {e}")
                logs.append(log)

    undispatched_tasks_count = len(not_done)

    return {'log_list': logs, 'done_count': len(done), 'not_done_count': undispatched_tasks_count}

# Connect to listen the queue and continuously consume the tasks
def _consume_queue(queue, config, timeout_unixtime, frequency_interval_seconds) -> dict:
    log_return_batch = []
    while True:
        if timeout_unixtime and (time.time() > timeout_unixtime):
            if config['verbose']: print(f'Dispatcher timeout reached, stopping consuming tasks...')
            break
        try:
            task = queue.get(timeout=frequency_interval_seconds+60) if frequency_interval_seconds else queue.get_nowait()
        except Queue.Empty:
            # print("Queue Empty")
            break  # If queue is empty, break the loop
        except Exception as e:
            print(f"Error accessing queue: {e}")
            break
        else:
            if task == None:    # For frequency mode case
                break
            result = _consume_task(task, config)
            log_return_batch.append(result)
    
    return log_return_batch

# Single Task function
def _consume_task(data, config) -> dict:
    started_at = time.time()
    task_rewrite_data = {}
    return_value = config['task']['callback'](config=config['task']['config'], id=data['id'], task=data['task'], log=task_rewrite_data)
    ended_at = time.time()
    duration = ended_at - started_at
    log = {
        'task_id': data['id'],
        'started_at': started_at,
        'ended_at': ended_at,
        'duration': duration,
        'result': return_value,
        'log': task_rewrite_data,
    }
    return log

def _merge_dicts_recursive(default_dict, user_dict):
    merged_dict = copy.deepcopy(default_dict)
    for key, user_value in user_dict.items():
        if key in merged_dict:
            if isinstance(merged_dict[key], dict) and isinstance(user_value, dict):
                merged_dict[key] = _merge_dicts_recursive(merged_dict[key], user_value)
            else:
                merged_dict[key] = user_value
        else:
            merged_dict[key] = user_value
    
    return merged_dict

# TPS report
def get_tps(
    logs: dict = None,
    debug: bool = False,
    interval: float = 0,
    reverse_interval: bool = False,
    display_intervals: bool = False
) -> dict:
    
    # Logs data check
    logs = logs if logs else get_logs()
    if not isinstance(logs, list):
        return False
    
    # Run the TPS for all
    success_id_set = set()
    main_tps_data = _tps_calculate(False, False, logs, True, success_id_set)

    started_at = main_tps_data['started_at']
    ended_at = main_tps_data['ended_at']
    success_count = main_tps_data['count']['success']
    exec_time_avg = main_tps_data['metrics']['execution_time']['avg']
    tps = main_tps_data['tps']

    # Peak data definition
    peak_tps_data = {}
    
    # Compile Intervals and find peak TPS
    interval_log_list = []
    if success_count > 0:
        interval = interval if interval else round(exec_time_avg * 3, 2) if (exec_time_avg * 3) >= 1 else 5
        interval_success_count = 0
        interval_ended_at = ended_at
        # Reserve option
        interval_pointer = interval_ended_at if reverse_interval else started_at
        interval = interval * -1 if reverse_interval else interval
        while started_at <= interval_pointer <= ended_at:
            current_success_count = 0
            # Shift Indicator
            peak_current_pointer = interval_pointer
            interval_pointer += interval
            # Reserve option
            interval_ended_at = peak_current_pointer if reverse_interval else interval_pointer
            peak_started_at = interval_pointer if reverse_interval else peak_current_pointer
            interval_ended_at = ended_at if interval_ended_at > ended_at else interval_ended_at
            peak_started_at = started_at if peak_started_at < started_at else peak_started_at
            # Calculate TPS for each interval
            tps_data = _tps_calculate(peak_started_at, interval_ended_at, logs)
            interval_log_list.append(tps_data)
            # Find the peak
            current_success_count = int(tps_data['count']['success'])
            current_tps = float(tps_data['tps'])
            if debug: print(" - Interval - Start Time: {}, End Time: {}, TPS: {}".format(peak_started_at, interval_ended_at, current_tps))
            if current_success_count and current_success_count > interval_success_count:
                interval_success_count = current_success_count
                if debug: print("    * Find peak above the main TPS - Interval TPS: {}, Main TPS: {}".format(current_tps, tps))
                # Comparing with the main TPS
                if current_tps > tps:
                    peak_tps_data = tps_data

    # Peak Finding Algorithm
    start_time_counts = {}
    tps_threshold = float(peak_tps_data['tps']) if peak_tps_data else tps
    peak_started_time = 0
    peak_ended_time = 0
    for log in logs:
        if log['task_id'] in success_id_set:
            key = str(math.floor(log['started_at']))
            start_time_counts[key] = start_time_counts[key] + 1 if key in start_time_counts else 1
    # Each Calculation
    for start_time, count in start_time_counts.items():
        start_time = int(start_time)
        if count <= tps_threshold:
            continue
        if debug: print("- Peak Finding - Start Time: {}, Count: {}, Current TPS Threshold: {}".format(start_time, count, tps_threshold))
        duration_count = 0
        remaining_count = count
        while (start_time + duration_count) <= math.ceil(ended_at):
            success_count = 0
            duration_count = ended_at - start_time if (start_time + duration_count) > ended_at else duration_count + 1
            for log in logs:
                if math.floor(log['started_at']) >= start_time and math.ceil(log['ended_at']) <= start_time + duration_count:
                    success_count += 1
                    remaining_count -= 1
            # Check Finest
            cuurent_tps = success_count / duration_count
            # Find the peak
            if cuurent_tps > tps_threshold:
                tps_threshold = cuurent_tps
                peak_started_time = start_time
                peak_ended_time = start_time + duration_count
                if debug: print("    * Find Peak - TPS: {}, Started at: {}, Ended at: {}".format(tps_threshold, peak_started_time, peak_ended_time))     
    # Aggregation
    if peak_started_time or peak_ended_time:
        peak_tps_data = _tps_calculate(peak_started_time, peak_ended_time, logs)

    # TPS data compilation
    main_tps_data['peak'] = peak_tps_data
    if display_intervals:
        main_tps_data['intervals'] = interval_log_list

    return main_tps_data

def _tps_calculate(started_at: float, ended_at: float, logs: list, display_validity: bool=False, success_id_set: set=None) -> dict:
    log_started_at = 0
    log_ended_at = 0
    exec_time_sum = 0
    exec_time_max = 0
    exec_time_min = 0
    exec_time_success_sum = 0
    exec_time_success_max = 0
    exec_time_success_min = 0
    total_count = 0
    invalid_count = 0
    current_success_count = 0
    task_start_count = 0
    task_end_count = 0
    for log in logs:
        if not _validate_log_format(log):
            invalid_count += 1
            continue
        # If given a interval
        if started_at and ended_at:
            # The full interval given will account for duplicate counts in the next interval
            task_start_count = task_start_count + 1 if started_at <= log['started_at'] < ended_at else task_start_count
            task_end_count = task_end_count + 1 if started_at < log['ended_at'] <= ended_at else task_end_count
        elif started_at:
            task_start_count = task_start_count + 1 if started_at <= log['started_at'] else task_start_count
            task_end_count = task_end_count + 1 if started_at <= log['ended_at'] else task_end_count
        elif ended_at:
            task_start_count = task_start_count + 1 if log['started_at'] <= ended_at else task_start_count
            task_end_count = task_end_count + 1 if log['ended_at'] <= ended_at else task_end_count
        if (started_at and log['started_at'] < started_at) or (ended_at and log['ended_at'] > ended_at):
            continue
        total_count += 1
        # Calculate log period
        log_started_at = log['started_at'] if log['started_at'] < log_started_at or log_started_at == 0 else log_started_at
        log_ended_at = log['ended_at'] if log['ended_at'] > log_ended_at else log_ended_at
        exec_time = log['duration'] if 'duration' in log else log['ended_at'] - log['started_at']
        exec_time_sum += exec_time
        exec_time_max = exec_time if not exec_time_max or exec_time > exec_time_max else exec_time_max
        exec_time_min = exec_time if not exec_time_min or exec_time < exec_time_min else exec_time_min
        # Success case check
        result = log['result']
        if (isinstance(result, requests.Response) and result.status_code != 200) or not result:
            continue
        # Rewrite success_id_set if in the argument
        if isinstance(success_id_set, set):
            success_id_set.add(log['task_id'])
        current_success_count += 1           
        exec_time_success_sum += exec_time
        exec_time_success_max = exec_time if not exec_time_success_max or exec_time > exec_time_success_max else exec_time_success_max
        exec_time_success_min = exec_time if not exec_time_success_min or exec_time < exec_time_success_min else exec_time_success_min
    
    # current_valid_count = total_count - current_invalid_count
    exec_time_avg = exec_time_sum / total_count if exec_time_sum else 0
    exec_time_success_avg = exec_time_success_sum / current_success_count if exec_time_success_sum else 0
    # Specify whether to use an interval period or a log period
    if not started_at:
        task_start_count = total_count
        started_at = log_started_at
    if not ended_at:
        task_end_count = total_count
        ended_at = log_ended_at
    duration = ended_at - started_at
    current_tps = current_success_count / duration if duration > 0 else 0
    tps_data = {
        'tps': round(current_tps, 2),
        'started_at': started_at,
        'ended_at': ended_at,
        'duration': duration,
        'metrics': {
            'execution_time': {
                'avg': exec_time_avg,
                'max': exec_time_max,
                'min': exec_time_min
            },
            'success_execution_time': {
                'avg': exec_time_success_avg,
                'max': exec_time_success_max,
                'min': exec_time_success_min
            },
        },
        'count': {
            'success': current_success_count,
            'total': total_count,
            'start': task_start_count,
            'end': task_end_count,
        },
    }
    if display_validity:
        tps_data['count']['invalidity'] = total_count - invalid_count
    return tps_data

def _validate_log_format(log) -> bool:
    return all(key in log for key in ('started_at', 'ended_at', 'result'))

def get_results() -> list:
    return results

def get_logs() -> list:
    return logs

def get_result_info() -> dict:
    return result_info

def get_duration() -> float:
    return result_info['started_at'] if 'started_at' in result_info else None