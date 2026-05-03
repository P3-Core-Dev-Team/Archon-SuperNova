package com.archon.openmetadata.iam.controllers;

import com.archon.openmetadata.iam.dto.UserDto;
import com.archon.openmetadata.iam.dto.UserFilterBean;
import com.archon.openmetadata.iam.models.User;
import com.archon.openmetadata.iam.services.UserService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/users")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class UserController {

  private final UserService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<User> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<UserDto>>> fetchAll(Pageable pageable) {
    Page<User> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, UserDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(UserController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<UserDto>> getById(@PathVariable UUID id) {
    User entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, UserDto.class),
            WebMvcLinkBuilder.linkTo(WebMvcLinkBuilder.methodOn(UserController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<UserDto>> create(@RequestBody UserDto dto) {
    User saved = service.save(modelMapper.map(dto, User.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, UserDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(UserController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<UserDto>> update(
      @PathVariable UUID id, @RequestBody UserDto dto) {
    dto.setId(id);
    User updated = service.save(modelMapper.map(dto, User.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, UserDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(UserController.class).getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<UserDto>>> searchAll(
      @RequestBody UserFilterBean filterBean, Pageable pageable) {
    Page<User> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();

              // Example of inline explicit criteria matching your requirement
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                // Replace "id" with actual string fields you want to search
                predicates.add(
                    criteriaBuilder.like(
                        root.get("id").as(String.class), "%" + filterBean.getSearchText() + "%"));
              }

              if (predicates.isEmpty()) {
                return criteriaBuilder.conjunction();
              }
              return criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, UserDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(UserController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
