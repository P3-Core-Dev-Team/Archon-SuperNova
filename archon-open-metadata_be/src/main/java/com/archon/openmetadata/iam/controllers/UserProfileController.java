package com.archon.openmetadata.iam.controllers;

import com.archon.openmetadata.iam.dto.UserDto;
import com.archon.openmetadata.iam.dto.UserPreferenceDto;
import com.archon.openmetadata.iam.models.User;
import com.archon.openmetadata.iam.models.UserPreference;
import com.archon.openmetadata.iam.repositories.UserPreferenceRepository;
import com.archon.openmetadata.iam.repositories.UserRepository;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/profile")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class UserProfileController {

    private final UserRepository userRepository;
    private final UserPreferenceRepository userPreferenceRepository;

    @GetMapping
    public ResponseEntity<?> getMyProfile() {
        // Mocking logged in user for now (e.g. "admin")
        User user = userRepository.findAll().stream().findFirst().orElse(null);
        if (user == null) {
            return ResponseEntity.notFound().build();
        }

        UserPreference pref = userPreferenceRepository.findByUserId(user.getId())
                .orElse(new UserPreference());

        Map<String, Object> response = new HashMap<>();
        response.put("user", mapToDto(user));
        response.put("preferences", mapToPrefDto(pref));
        
        return ResponseEntity.ok(response);
    }

    @PutMapping("/preferences")
    public ResponseEntity<?> updatePreferences(@RequestBody UserPreferenceDto dto) {
        User user = userRepository.findAll().stream().findFirst().orElse(null);
        if (user == null) {
            return ResponseEntity.notFound().build();
        }

        UserPreference pref = userPreferenceRepository.findByUserId(user.getId())
                .orElseGet(() -> {
                    UserPreference newPref = new UserPreference();
                    newPref.setUser(user);
                    return newPref;
                });

        pref.setTheme(dto.getTheme() != null ? dto.getTheme() : pref.getTheme());
        pref.setDateFormat(dto.getDateFormat() != null ? dto.getDateFormat() : pref.getDateFormat());
        pref.setTimezone(dto.getTimezone() != null ? dto.getTimezone() : pref.getTimezone());
        pref.setDefaultGraphView(dto.getDefaultGraphView() != null ? dto.getDefaultGraphView() : pref.getDefaultGraphView());

        userPreferenceRepository.save(pref);

        return ResponseEntity.ok(mapToPrefDto(pref));
    }
    
    @PutMapping("/details")
    public ResponseEntity<?> updateDetails(@RequestBody UserDto dto) {
        User user = userRepository.findAll().stream().findFirst().orElse(null);
        if (user == null) {
            return ResponseEntity.notFound().build();
        }
        
        // Update basic info
        if (dto.getUsername() != null) {
            user.setUsername(dto.getUsername());
        }
        userRepository.save(user);
        
        return ResponseEntity.ok(mapToDto(user));
    }

    private UserDto mapToDto(User user) {
        UserDto dto = new UserDto();
        dto.setId(user.getId());
        dto.setUsername(user.getUsername());
        dto.setEmail(user.getEmail());
        dto.setRole(user.getRole());
        dto.setStatus(user.getStatus());
        dto.setGroups(user.getGroups() != null ? user.getGroups().stream().map(g -> {
            com.archon.openmetadata.iam.dto.GroupDto gd = new com.archon.openmetadata.iam.dto.GroupDto();
            gd.setGroupName(g.getGroupName());
            return gd;
        }).collect(java.util.stream.Collectors.toList()) : java.util.Collections.emptyList());
        return dto;
    }

    private UserPreferenceDto mapToPrefDto(UserPreference pref) {
        UserPreferenceDto dto = new UserPreferenceDto();
        dto.setId(pref.getId());
        dto.setTheme(pref.getTheme());
        dto.setDateFormat(pref.getDateFormat());
        dto.setTimezone(pref.getTimezone());
        dto.setDefaultGraphView(pref.getDefaultGraphView());
        return dto;
    }
}
