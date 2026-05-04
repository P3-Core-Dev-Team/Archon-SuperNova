package com.archon.openmetadata.job.services;

import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class SseBroadcasterService {

    private final Map<UUID, SseEmitter> emitters = new ConcurrentHashMap<>();

    public SseEmitter createEmitter(UUID jobId) {
        // 30 minute timeout
        SseEmitter emitter = new SseEmitter(30 * 60 * 1000L);
        emitters.put(jobId, emitter);

        emitter.onCompletion(() -> emitters.remove(jobId));
        emitter.onTimeout(() -> emitters.remove(jobId));
        emitter.onError(e -> emitters.remove(jobId));

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
