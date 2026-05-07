package com.archon.openmetadata.job.services;

import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class SseBroadcasterService {

    private final Map<UUID, SseEmitter> emitters = new ConcurrentHashMap<>();
    private final Map<Long, List<Object>> eventHistory = new ConcurrentHashMap<>();

    public SseEmitter createEmitter(UUID jobId) {
        // 30 minute timeout
        SseEmitter emitter = new SseEmitter(30 * 60 * 1000L);
        emitters.put(jobId, emitter);

        emitter.onCompletion(() -> emitters.remove(jobId));
        emitter.onTimeout(() -> emitters.remove(jobId));
        emitter.onError(e -> emitters.remove(jobId));

        return emitter;
    }
    public SseEmitter subscribe(UUID id) {
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

    public void broadcast(UUID jobId, String eventName, Object data) {
        SseEmitter emitter = emitters.get(jobId);
        if (emitter != null) {
            try {
                emitter.send(SseEmitter.event()
                        .name(eventName)
                        .data(data));
            } catch (IOException e) {
                emitters.remove(jobId);
            }
        }
    }

    public void complete(UUID jobId) {
        SseEmitter emitter = emitters.get(jobId);
        if (emitter != null) {
            emitter.complete();
            emitters.remove(jobId);
        }
    }
}
