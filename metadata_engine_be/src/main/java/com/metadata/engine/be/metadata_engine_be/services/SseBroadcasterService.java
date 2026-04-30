package com.metadata.engine.be.metadata_engine_be.services;

import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

import lombok.extern.slf4j.Slf4j;

@Slf4j
@Service
public class SseBroadcasterService {
    private final Map<Long, SseEmitter> emitters = new ConcurrentHashMap<>();
    private final Map<Long, List<Object>> eventHistory = new ConcurrentHashMap<>();

    public SseEmitter subscribe(Long id) {
        SseEmitter emitter = new SseEmitter(0L); // No timeout
        emitters.put(id, emitter);
        emitter.onCompletion(() -> emitters.remove(id));
        emitter.onTimeout(() -> emitters.remove(id));
        
        // Dispatch history to new subscriber immediately
        List<Object> history = eventHistory.get(id);
        if (history != null) {
            for (Object data : history) {
                try {
                    emitter.send(org.springframework.web.servlet.mvc.method.annotation.SseEmitter.event()
                            .name("message")
                            .data(data, org.springframework.http.MediaType.APPLICATION_JSON));
                } catch (Exception e) {
                    emitters.remove(id);
                    break;
                }
            }
        }
        return emitter;
    }

    public void sendEvent(Long jobId, Object data) {
        log.info("[SSE-JOB-{}] UI Transition Hook: {}", jobId, data);
        eventHistory.computeIfAbsent(jobId, k -> new CopyOnWriteArrayList<>()).add(data);
        
        SseEmitter emitter = emitters.get(jobId);
        if (emitter != null) {
            try {
                emitter.send(org.springframework.web.servlet.mvc.method.annotation.SseEmitter.event()
                        .name("message")
                        .data(data, org.springframework.http.MediaType.APPLICATION_JSON));
                Thread.sleep(300); // Simulate processing latency for UI animation buffers
            } catch (Exception e) {
                emitters.remove(jobId);
            }
        }
    }

    public void clearHistory(Long jobId) {
        eventHistory.remove(jobId);
        SseEmitter emitter = emitters.remove(jobId);
        if (emitter != null) {
            emitter.complete();
        }
    }
}
